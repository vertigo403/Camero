import json
from pathlib import Path
import re
import subprocess
import unittest

from camero.scanner import _parse_recorded_at
from camero.settings import DEFAULT_FILENAME_REGEX


class FilenameDefaultsTests(unittest.TestCase):
    def test_default_metadata(self):
        pattern = re.compile(DEFAULT_FILENAME_REGEX)
        for filename, streamer, site, date, time, ext in [
            ("LumaVox_Camturbate_20250112_213015.mp4", "LumaVox", "Camturbate", "20250112", "213015", "mp4"),
            ("Luma_Vox_Camturbate_20250112_213015_part.001.mkv", "Luma_Vox", "Camturbate", "20250112", "213015", "mkv"),
            ("elliemint_2026-02-12_03-35-14_045.ts", "elliemint", None, "2026-02-12", "03-35-14", "ts"),
            ("ellie_mint_2026-02-12_03-35-14_045.ts", "ellie_mint", None, "2026-02-12", "03-35-14", "ts"),
        ]:
            with self.subTest(filename=filename):
                match = pattern.fullmatch(filename)
                self.assertIsNotNone(match)
                self.assertEqual(match.groupdict(), dict(
                    streamer=streamer, site=site, date=date, time=time, ext=ext,
                ))
                recorded_at = _parse_recorded_at(date, time)
                self.assertIsNotNone(recorded_at)
                self.assertEqual(recorded_at.strftime("%Y%m%d%H%M%S"),
                                 re.sub(r"\D", "", date + time))

    def test_default_rejects_missing_timestamp(self):
        for filename in ["video.mp4", "name_site_20250112.mp4", "name_2026-02-12_bad.ts"]:
            with self.subTest(filename=filename):
                self.assertIsNone(re.fullmatch(DEFAULT_FILENAME_REGEX, filename))

    def test_ui_default_generates_backend_regex(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "camero" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        template = re.search(r"const DEFAULT_FILENAME_TEMPLATE = ('[^']+');", source).group(1)
        escape = source[source.index("  function escapeRegexLiteral("):source.index("  function insertAtCursor(")]
        builder = source[source.index("  function buildRegexFromTemplate("):source.index("  function templateStorageKeyForRow(")]
        script = escape + builder + f"\nconsole.log(JSON.stringify(buildRegexFromTemplate({template})));"
        result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "regex": DEFAULT_FILENAME_REGEX})


if __name__ == "__main__":
    unittest.main()
