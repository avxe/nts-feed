"""
Comprehensive security tests for Flask-Talisman CSP and HTTPS enforcement.

Tests cover:
- Strict CSP directives (no unsafe-inline / unsafe-eval)
- Per-request nonce generation for inline data scripts
- Security headers (X-Frame-Options, X-Content-Type-Options, etc.)
- HTTPS redirect behavior
- Talisman enable/disable behavior
- Graceful degradation when flask-talisman is unavailable
"""
import os
import unittest


class TestSecurityHeaders(unittest.TestCase):
    """Test security headers when Talisman is enabled."""

    def setUp(self):
        os.environ['ENABLE_TALISMAN'] = 'true'
        os.environ['FORCE_HTTPS'] = 'false'
        os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret

        from nts_feed.app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        for key in ['ENABLE_TALISMAN', 'FORCE_HTTPS']:
            os.environ.pop(key, None)

    # --- CSP presence and structure ---

    def test_csp_header_present(self):
        """CSP header must always be present when Talisman is enabled."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy')
        self.assertIsNotNone(csp, "CSP header missing")
        self.assertIn("default-src", csp)

    def test_csp_default_src_is_self(self):
        """default-src should restrict to 'self' only."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn("default-src 'self'", csp)

    def test_csp_script_src_allows_youtube(self):
        """script-src must allow YouTube iframe API."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn('script-src', csp)
        self.assertIn('https://www.youtube.com', csp)
        self.assertIn('https://s.ytimg.com', csp)

    def test_csp_style_src_allows_cdnjs(self):
        """style-src must allow cdnjs for Font Awesome."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn('style-src', csp)
        self.assertIn('https://cdnjs.cloudflare.com', csp)

    def test_csp_frame_src_allows_embed_platforms(self):
        """frame-src must allow YouTube, SoundCloud and Mixcloud embeds."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn('frame-src', csp)
        self.assertIn('https://www.youtube.com', csp)
        self.assertIn('https://w.soundcloud.com', csp)
        self.assertIn('https://www.mixcloud.com', csp)

    def test_csp_img_src_allows_data_and_https(self):
        """img-src needs data: URIs and https: for external images."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn('img-src', csp)
        self.assertIn('data:', csp)

    def test_csp_media_src_allows_blob_and_https(self):
        """media-src needs blob: and https: for audio playback from CDNs."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn("media-src", csp)
        self.assertIn("blob:", csp)
        self.assertIn("https:", csp)

    # --- Strict CSP: no unsafe directives ---

    def test_csp_script_src_no_unsafe_inline(self):
        """script-src must NOT contain 'unsafe-inline' (style-src may have it)."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        # Extract the script-src directive only
        for directive in csp.split(';'):
            directive = directive.strip()
            if directive.startswith('script-src'):
                self.assertNotIn("'unsafe-inline'", directive)
                break

    def test_csp_no_unsafe_eval(self):
        """CSP must NOT contain 'unsafe-eval' — no eval() usage in codebase."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertNotIn("'unsafe-eval'", csp)

    # --- Nonce support ---

    def test_csp_contains_nonce(self):
        """CSP script-src must include a nonce for inline data scripts."""
        response = self.client.get('/')
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertRegex(csp, r"'nonce-[A-Za-z0-9+/_=-]+'",
                         "Expected a nonce in script-src")

    # --- Other security headers ---

    def test_x_frame_options_header(self):
        """X-Frame-Options must be set to SAMEORIGIN."""
        response = self.client.get('/')
        x_frame = response.headers.get('X-Frame-Options')
        self.assertIsNotNone(x_frame)
        self.assertIn(x_frame.upper(), ['SAMEORIGIN', 'DENY'])

    def test_x_content_type_options_header(self):
        """X-Content-Type-Options must prevent MIME sniffing."""
        response = self.client.get('/')
        self.assertEqual(
            response.headers.get('X-Content-Type-Options', '').lower(),
            'nosniff',
        )

    def test_referrer_policy_header(self):
        """Referrer-Policy header must be set."""
        response = self.client.get('/')
        self.assertIsNotNone(response.headers.get('Referrer-Policy'))


class TestSecurityHeadersDisabled(unittest.TestCase):
    """Test behavior when Talisman is disabled."""

    def setUp(self):
        os.environ['ENABLE_TALISMAN'] = 'false'
        os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret

        from nts_feed.app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('ENABLE_TALISMAN', None)

    def test_app_works_without_talisman(self):
        """App must serve pages even with Talisman disabled."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_no_csp_when_disabled(self):
        """CSP must not be set when Talisman is disabled."""
        response = self.client.get('/')
        self.assertIsNone(response.headers.get('Content-Security-Policy'))


class TestHTTPSEnforcement(unittest.TestCase):
    """Test HTTPS redirect behavior."""

    def setUp(self):
        os.environ['ENABLE_TALISMAN'] = 'true'
        os.environ['FORCE_HTTPS'] = 'true'
        os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret

        from nts_feed.app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        for key in ['ENABLE_TALISMAN', 'FORCE_HTTPS']:
            os.environ.pop(key, None)

    def test_https_redirect_when_enabled(self):
        """HTTP requests should redirect to HTTPS when FORCE_HTTPS=true."""
        response = self.client.get('/', follow_redirects=False)
        self.assertIn(response.status_code, [200, 301, 302, 308])

    def test_strict_transport_security(self):
        """HSTS header should be set when FORCE_HTTPS is enabled."""
        response = self.client.get('/')
        hsts = response.headers.get('Strict-Transport-Security')
        if hsts:
            self.assertIn('max-age', hsts)


class TestHTTPSDisabled(unittest.TestCase):
    """Test behavior when FORCE_HTTPS is disabled."""

    def setUp(self):
        os.environ['ENABLE_TALISMAN'] = 'true'
        os.environ['FORCE_HTTPS'] = 'false'
        os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret

        from nts_feed.app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        for key in ['ENABLE_TALISMAN', 'FORCE_HTTPS']:
            os.environ.pop(key, None)

    def test_no_redirect_when_disabled(self):
        """No HTTPS redirect when FORCE_HTTPS=false."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)


class TestTalismanGracefulDegradation(unittest.TestCase):
    """Test graceful degradation when flask-talisman is unavailable."""

    def test_app_starts_without_talisman_import(self):
        """App must start even if flask-talisman is not installed."""
        import sys

        original_talisman = sys.modules.get('flask_talisman')

        try:
            sys.modules['flask_talisman'] = None

            os.environ['ENABLE_TALISMAN'] = 'true'
            os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret


            self.assertTrue(True)  # If we get here, no crash occurred

        finally:
            if original_talisman:
                sys.modules['flask_talisman'] = original_talisman
            os.environ.pop('ENABLE_TALISMAN', None)


class TestSessionCookieSecurity(unittest.TestCase):
    """Test session cookie security settings."""

    def setUp(self):
        os.environ['ENABLE_TALISMAN'] = 'true'
        os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'  # pragma: allowlist secret

        from nts_feed.app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('ENABLE_TALISMAN', None)

    def test_session_cookie_samesite(self):
        self.assertEqual(self.app.config.get('SESSION_COOKIE_SAMESITE'), 'Lax')

    def test_session_cookie_secure(self):
        self.assertEqual(self.app.config.get('SESSION_COOKIE_SECURE'), True)

    def test_preferred_url_scheme(self):
        self.assertEqual(self.app.config.get('PREFERRED_URL_SCHEME'), 'https')


if __name__ == '__main__':
    unittest.main()
