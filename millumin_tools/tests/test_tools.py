"""Run: python3 -m unittest discover millumin_tools/tests"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "millumin_mcp"), str(ROOT / "content_check")]

import check_content  # noqa: E402
from osc import decode_packet, encode_message, parse_hosts  # noqa: E402


class OscTests(unittest.TestCase):
    def test_round_trip(self):
        pkt = encode_message("/action/launchColumn", 3, 0.5, "Intro", True)
        self.assertEqual(len(pkt) % 4, 0)
        self.assertEqual(decode_packet(pkt), [("/action/launchColumn", [3, 0.5, "Intro", 1])])

    def test_no_args(self):
        self.assertEqual(decode_packet(encode_message("/action/stopColumn")), [("/action/stopColumn", [])])

    def test_parse_hosts(self):
        self.assertEqual(parse_hosts("10.0.0.11:5000, 10.0.0.12"), [("10.0.0.11", 5000), ("10.0.0.12", 5000)])


class McpServerTests(unittest.TestCase):
    def test_tools_send_osc_to_all_hosts(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        socks = []
        for _ in range(2):  # pretend main + backup Macs
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.settimeout(5)
            socks.append(s)
        hosts = ",".join(f"127.0.0.1:{s.getsockname()[1]}" for s in socks)
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "millumin_mcp" / "server.py")],
                                       env={**os.environ, "MILLUMIN_HOSTS": hosts}, cwd=str(ROOT / "millumin_mcp"))

        async def run():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    self.assertTrue({"launch_column", "next_column", "set_master", "send_osc", "status"} <= names)
                    await session.call_tool("launch_column", {"column": 4})
                    await session.call_tool("set_layer_opacity", {"layer": "Logo", "opacity": 0.25})
                    await session.call_tool("set_master", {"kind": "video", "level": 0})

        asyncio.run(run())
        expected = [("/action/launchColumn", [4]), ("/layer:Logo/opacity", [0.25]), ("/masterVideo", [0.0])]
        for s in socks:
            got = [decode_packet(s.recvfrom(1024)[0])[0] for _ in expected]
            self.assertEqual(got, expected)
            s.close()


def minimal_pdf() -> bytes:
    """A one-page 16:9 PDF, which is what a PDF-compatible .ai file contains."""
    content = b"1 0 0 rg 100 100 400 200 re f"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def make(self, name, src, *extra):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src, "-t", "1", *extra, str(self.dir / name)], check=True)

    def by_name(self, items):
        return {i.rel: i for i in items}

    def test_flags(self):
        self.make("clip25.mp4", "testsrc2=s=640x360:r=25", "-c:v", "libx264")
        self.make("clip60.mov", "testsrc2=s=1920x1080:r=60", "-c:v", "prores_ks")
        (self.dir / "deck.key").touch()
        items = self.by_name(check_content.scan(self.dir, 60, (1920, 1080)))
        msgs = " ".join(m for _, m in items["clip25.mp4"].issues)
        self.assertIn("judder", msgs)
        self.assertIn("delivery codec", msgs)
        self.assertEqual(items["clip60.mov"].worst, "OK")
        self.assertEqual(items["deck.key"].worst, "ERROR")

    def test_unsupported_alpha_and_illustrator(self):
        self.make("anim_Alpha.mov", "color=c=red@0.5:s=640x360:r=60,format=rgba", "-c:v", "qtrle")
        (self.dir / "broken.mov").touch()
        (self.dir / "screen.ai").write_bytes(minimal_pdf())
        items = self.by_name(check_content.scan(self.dir, 60, None))
        self.assertEqual(items["anim_Alpha.mov"].worst, "ERROR")
        self.assertTrue(items["anim_Alpha.mov"].alpha)
        self.assertIn("cloud", items["broken.mov"].issues[-1][1])
        self.assertEqual((items["screen.ai"].kind, items["screen.ai"].worst), ("vector", "ERROR"))

        out = self.dir / "out"
        failures = check_content.convert(list(items.values()), out, "auto", 60, False, None, False)
        converted = self.by_name(check_content.scan(out, 60, None))
        self.assertEqual(converted["anim_Alpha.mov"].codec, "hap_alpha")
        if shutil.which("pdftocairo"):
            self.assertEqual(failures, 1)  # only broken.mov
            self.assertEqual((converted["screen.png"].width, converted["screen.png"].height), (1920, 1080))

    def test_convert_to_hap(self):
        self.make("in.mp4", "testsrc2=s=642x360:r=30", "-c:v", "libx264")
        items = check_content.scan(self.dir, 60, None)
        out = self.dir / "out"
        self.assertEqual(check_content.convert(items, out, "hap", 60, True, None, False), 0)
        res = self.by_name(check_content.scan(out, 60, None))["in.mov"]
        self.assertEqual((res.codec, res.width, res.fps), ("hap", 644, 60.0))


if __name__ == "__main__":
    unittest.main()
