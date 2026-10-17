import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "embeddinggemma-2-text-vision-440m.litertlm"


@unittest.skipUnless(
    MODEL.is_file() and importlib.util.find_spec("mediapipe") is not None,
    "Requires MediaPipe and the local EmbeddingGemma model",
)
class SemanticLoggingTests(unittest.TestCase):
    def test_native_info_and_warnings_are_suppressed_without_changing_embeddings(self):
        script = """
import io
import json
from pathlib import Path
import sys
from PIL import Image

sys.path.insert(0, str(Path.cwd() / "src"))
from camero.semantic_search import _semantic_search_searcher
from mediapipe.tasks.python.core import mediapipe_c_bindings

embedder, retriever = _semantic_search_searcher(Path(sys.argv[1]), Path(":memory:"))
with embedder, retriever:
    if sys.argv[2] == "info":
        mediapipe_c_bindings.load_raw_library().litert_lm_set_min_log_level(2)
    image = io.BytesIO()
    Image.new("RGB", (1920, 1200), (40, 80, 120)).save(image, format="JPEG")
    result = embedder.embed_image(image.getvalue())
    print(json.dumps(result.embeddings[0].embedding.tolist()))
"""
        results = {}
        for level in ("info", "error"):
            with self.subTest(level=level):
                results[level] = subprocess.run(
                    [sys.executable, "-c", script, str(MODEL), level],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=True,
                )
        self.assertIn("Resize image from 1920x1200", results["info"].stderr)
        self.assertNotIn("Resize image from", results["error"].stderr)
        self.assertNotIn("Logging before InitGoogle()", results["error"].stderr)
        self.assertNotIn("Section not found:", results["error"].stderr)
        self.assertNotIn("TFLite model type:", results["error"].stderr)
        self.assertNotIn("Missing 10 bands", results["error"].stderr)
        self.assertNotIn("TFLite model for type:", results["error"].stderr)
        baseline = json.loads(results["info"].stdout)
        quiet = json.loads(results["error"].stdout)
        self.assertEqual(len(quiet), 768)
        for expected, actual in zip(baseline, quiet):
            self.assertAlmostEqual(expected, actual, places=6)


if __name__ == "__main__":
    unittest.main()
