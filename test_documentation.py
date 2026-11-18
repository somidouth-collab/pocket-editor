"""Offline documentation coverage, HTML links, source rendering, and Help action."""
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('pocket_build_docs', ROOT / 'tools/documentation/build_docs.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.ids = []; self.links = []; self.assets = []; self.code = []
        self.in_code = 0; self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if 'id' in attributes: self.ids.append(attributes['id'])
        if tag == 'a' and attributes.get('href'): self.links.append(attributes['href'])
        if tag in ('script', 'img') and attributes.get('src'): self.assets.append(attributes['src'])
        if tag == 'link' and attributes.get('href'): self.assets.append(attributes['href'])
        if tag == 'code': self.in_code += 1

    def handle_endtag(self, tag):
        if tag == 'code': self.in_code -= 1

    def handle_data(self, text):
        if self.in_code: self.code.append(text)


class DocumentationTests(unittest.TestCase):
    def test_pages_links_anchors_and_assets(self):
        pages = {path: Page(path.read_text(encoding='utf-8')) for path in (ROOT / 'docs').glob('*.html')}
        self.assertEqual(len(pages), len(builder.PAGES))
        for path, page in pages.items():
            with self.subTest(page=path.name):
                self.assertEqual(len(page.ids), len(set(page.ids)))
                for link in page.links + page.assets:
                    parsed = urlsplit(link)
                    if parsed.scheme or parsed.netloc:
                        self.assertNotIn(link, page.assets, 'Assets must not require network access')
                        continue
                    target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                    self.assertTrue(target.is_file(), f'{path.name}: broken target {link}')
                    if parsed.fragment and target.suffix == '.html':
                        target_page = pages.get(target) or Page(target.read_text(encoding='utf-8'))
                        self.assertIn(unquote(parsed.fragment), target_page.ids, f'{path.name}: missing anchor {link}')

    def test_search_index_and_all_plugins_are_covered(self):
        raw = (ROOT / 'docs/assets/search-index.js').read_text(encoding='utf-8')
        entries = json.loads(raw.removeprefix('window.POCKET_DOCS=').rstrip().removesuffix(';'))
        catalog = (ROOT / 'docs/plugins.html').read_text(encoding='utf-8')
        for path in (ROOT / 'pocket_editor_plugins').glob('*.py'):
            if not path.name.startswith('_'):
                self.assertIn(f'id="{path.stem}"', catalog)
                self.assertTrue(any(path.stem in entry['title'] for entry in entries))
        for entry in entries:
            filename, _, anchor = entry['url'].partition('#')
            target = ROOT / 'docs' / filename
            self.assertTrue(target.is_file())
            if anchor: self.assertIn(anchor, Page(target.read_text(encoding='utf-8')).ids)
        self.assertIn('Somi Douth', (ROOT / 'docs/index.html').read_text())
        self.assertIn('somidouth@gmail.com', catalog)

    def test_code_is_escaped_and_markdown_links_resolve(self):
        text = '# Sample\n\n```html\n<script>alert("example")</script>\n```\n\n[API](PLUGIN_DEVELOPMENT.md)\n'
        result, headings = builder.markdown(text, ROOT / 'README.md')
        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn('href="plugin-development.html"', result)
        self.assertIn('<script>alert("example")</script>', ''.join(Page(result).code))

    def test_generation_is_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(builder, 'OUT', Path(folder) / 'docs'):
                builder.build()
                first = {path.relative_to(builder.OUT): path.read_bytes() for path in builder.OUT.rglob('*') if path.is_file()}
                builder.build()
                second = {path.relative_to(builder.OUT): path.read_bytes() for path in builder.OUT.rglob('*') if path.is_file()}
        self.assertEqual(first, second)

    def test_help_opens_local_index_without_starting_server(self):
        import pocket_editor as core
        editor = type('EditorStub', (), {'root': None})()
        with patch('webbrowser.open', return_value=True) as browser:
            core.Editor.show_documentation(editor)
        self.assertEqual(browser.call_args.args[0], (ROOT / 'docs/index.html').as_uri())
        with patch('webbrowser.open', return_value=False), patch.object(core.messagebox, 'showinfo') as info:
            core.Editor.show_documentation(editor)
        info.assert_called_once()
