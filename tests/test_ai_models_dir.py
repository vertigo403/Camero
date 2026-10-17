import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from camero import autotag_jobs, semantic_search
from camero.settings import AppSettings, resolve_ai_models_dir


class AIModelsDirectoryTests(unittest.TestCase):
    def test_both_features_share_directory_resolution(self):
        for value in (None, "", "models", "~/camero-models"):
            with self.subTest(value=value):
                settings = AppSettings(ai_models_dir=value)
                model, tags = autotag_jobs._resolve_tagger_resource_paths(settings)
                directory = semantic_search.resolve_models_dir(value)
                self.assertEqual(model, directory / "model.onnx")
                self.assertEqual(tags, directory / "selected_tags.csv")
                self.assertEqual(directory, resolve_ai_models_dir(value))

    def test_directory_change_invalidates_tagger_signature(self):
        first = autotag_jobs._tagger_signature(AppSettings(ai_models_dir="first"))
        second = autotag_jobs._tagger_signature(AppSettings(ai_models_dir="second"))
        self.assertNotEqual(first, second)

    def test_missing_custom_resources_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(ai_models_dir=directory)
            with self.assertRaisesRegex(FileNotFoundError, "model.onnx"):
                autotag_jobs._create_tagger(settings)
            (Path(directory) / "model.onnx").touch()
            with self.assertRaisesRegex(FileNotFoundError, "selected_tags.csv"):
                autotag_jobs._create_tagger(settings)

    def test_explicit_model_is_not_replaced_by_bundled_model(self):
        source = Path(__file__).resolve().parents[1] / "src" / "autotagger" / "autotagger.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        functions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_model_path"
        ]
        requested = str(Path("custom-models").resolve() / "model.onnx")
        with patch("pathlib.Path.exists", return_value=True):
            namespace = {
                "Path": Path,
                "resolve_first_resource_path": lambda *paths: paths[0],
                "MODEL_PATH_FALLBACKS": ("assets/models/model.onnx",),
            }
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
            self.assertEqual(namespace["resolve_model_path"](requested), requested)


if __name__ == "__main__":
    unittest.main()
