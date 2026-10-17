import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from camero.app import create_app
from camero.main import CAMERO_VERSION


class HeaderVersionTests(unittest.TestCase):
    def test_header_shows_current_version_before_catalog(self):
        app = create_app()
        with patch("camero.app.load_settings") as settings:
            settings.return_value.auth_required = False
            response = TestClient(app).get("/")

        self.assertEqual(response.status_code, 200)
        version = f'<span id="cameroVersion">v{CAMERO_VERSION}</span>'
        self.assertIn(
            '<span>Camero</span>\n'
            '        <span class="brand-sep">|</span>\n'
            f'        {version}',
            response.text,
        )
        self.assertLess(response.text.index(version), response.text.index('id="catalogPaths"'))
        self.assertNotIn("{{CAMERO_VERSION}}", response.text)

    def test_header_escapes_version_from_constant(self):
        app = create_app()
        with (
            patch("camero.app.load_settings") as settings,
            patch("camero.main.CAMERO_VERSION", "2.1.0<test>"),
        ):
            settings.return_value.auth_required = False
            response = TestClient(app).get("/")

        self.assertIn('id="cameroVersion">v2.1.0&lt;test&gt;</span>', response.text)


if __name__ == "__main__":
    unittest.main()
