"""Curated public-release contents and author/manifest integrity checks."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_release import build_release, collect_files


class ReleaseTests(unittest.TestCase):
    def project(self, root):
        files={'pocket_editor.py':'print("editor")\n', 'editor_about.py':"DEFAULT_DETAILS = {'name':'Somi Douth','email':'somidouth@gmail.com'}\n",
               'README.md':'# Pocket Editor\n', 'docs/index.html':'<html>Pocket Editor</html>',
               'pocket_editor_plugins/team_project.py':'def register(api): pass\n',
               'pocket_editor_plugins/git_project.py':'def register(api): pass\n',
               'pocket_editor_plugins/ai_assistant.py':'print("chat")\n', 'pocket_editor_plugins/python_tools.py':'print("code")\n'}
        for name,content in files.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)

    def test_private_data_dependencies_and_unrelated_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.project(root)
            for name in ('client.team-invite','pocket_editor_session.json','pocket_editor_settings.json',
                         'pocket_editor_about.json','private_script.py','pocket_editor_plugins/git_project_settings.json',
                         'tools/php/node_modules/secret.js','tools/php/cache/private.json','audit/report.md','testp/private.py'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('PRIVATE-CONTENT')
            archive,_=build_release(root,root/'dist','test')
            with zipfile.ZipFile(archive) as release:
                self.assertFalse(any(b'PRIVATE-CONTENT' in release.read(name) for name in release.namelist()))
                self.assertNotIn('Pocket-Editor-test/private_script.py',release.namelist())

    def test_author_checksums_and_packaged_sources_are_consistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.project(root)
            archive,meta=build_release(root,root/'dist','test')
            self.assertEqual(meta['author']['name'],'Somi Douth')
            self.assertEqual(meta['author']['email'],'somidouth@gmail.com')
            with zipfile.ZipFile(archive) as release:
                data=json.loads(release.read('Pocket-Editor-test/RELEASE.json'))
                self.assertEqual(data['author'],meta['author'])
                for row in release.read('Pocket-Editor-test/MANIFEST.sha256').decode().splitlines():
                    digest,name=row.split('  ',1)
                    self.assertEqual(hashlib.sha256(release.read('Pocket-Editor-test/'+name)).hexdigest(),digest)
            self.assertEqual(archive.with_suffix('.zip.sha256').read_text().split()[0],hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_invalid_source_does_not_replace_previous_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.project(root)
            archive,_=build_release(root,root/'dist','test');before=archive.read_bytes()
            (root/'pocket_editor.py').write_text('def broken(')
            with self.assertRaises(SyntaxError):build_release(root,root/'dist','test')
            self.assertEqual(archive.read_bytes(),before)

    def test_release_version_cannot_escape_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.project(root)
            with self.assertRaises(ValueError):build_release(root,root/'dist','../escape')
