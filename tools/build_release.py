#!/usr/bin/env python3
"""Build a curated Pocket Editor source release without private runtime data."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_DOCS = {'README.md','TEAM_WORK.md','PLUGIN_DEVELOPMENT.md','AUTHOR.md','RELEASE_NOTES.md','PUBLISHING.md','INSTALL.md','PYPI_PUBLISHING.md','LICENSE','LICENSE.md','.gitignore'}
CORE_MODULES = {'pocket_editor.py','editor_about.py','editor_session.py','command_palette.py','quick_open.py','split_editor.py','plugin_manager.py','plugin_sidebar.py'}
EXTENSIONS = {'.fpf','.c','.cc','.cpp','.cxx','.h','.hpp','.py','.md','.html','.css','.js','.cjs','.mjs','.php','.json','.txt','.png','.svg','.toml','.in','.yml','.yaml'}
SKIP_DIRS = {'.git','__pycache__','node_modules','cache','dist','build','audit','old','testp','.venv','venv'}


def eligible(path):
    name=path.name.lower()
    return (not path.is_symlink() and not name.startswith('.env') and not name.endswith(('.team-invite','.pyc'))
            and not name.endswith('_settings.json') and not name.startswith('pocket_editor_session')
            and name!='pocket_editor_about.json')


def collect_files(root=ROOT):
    root=Path(root)
    files=[]
    for path in root.iterdir():
        if path.is_file() and eligible(path) and (path.name in CORE_MODULES or (path.name.startswith('test_') and path.suffix=='.py') or path.name in ROOT_DOCS):files.append(path)
    for directory in ('pocket_editor_plugins','tools','examples','docs','packaging','.github'):
        start=root/directory
        if not start.is_dir():continue
        for current, folders, names in os.walk(start,followlinks=False):
            folders[:]=sorted(name for name in folders if name not in SKIP_DIRS and not (Path(current)/name).is_symlink())
            for name in sorted(names):
                path=Path(current)/name
                if not eligible(path) or path.suffix not in EXTENSIONS:continue
                relative=path.relative_to(root)
                if directory=='pocket_editor_plugins' and path.suffix!='.py':continue
                if directory=='tools' and path.suffix=='.json' and relative.as_posix() not in ('tools/php/package.json','tools/php/package-lock.json'):continue
                files.append(path)
    return sorted(set(files),key=lambda path:path.relative_to(root).as_posix())


def author_details(root):
    tree=ast.parse((root/'editor_about.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='DEFAULT_DETAILS' for target in node.targets):
            values=ast.literal_eval(node.value)
            return {'name':values['name'],'email':values['email'],'website':values.get('website','')}
    raise ValueError('Published author information was not found.')


def build_release(root=ROOT,output=None,version=None):
    root=Path(root).resolve()
    version=version or datetime.now(timezone.utc).strftime('%Y.%m.%d')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,50}',version):raise ValueError('Invalid release version.')
    output=Path(output) if output else root/'dist'
    output.mkdir(parents=True,exist_ok=True)
    name='Pocket-Editor-'+version
    target=output/(name+'.zip')
    files=collect_files(root)
    required={'pocket_editor.py','plugin_sidebar.py','editor_about.py','README.md','docs/index.html',
              'pocket_editor_plugins/team_project.py','pocket_editor_plugins/git_project.py',
              'pocket_editor_plugins/ai_assistant.py','pocket_editor_plugins/python_tools.py'}
    present={path.relative_to(root).as_posix() for path in files}
    missing=required-present
    if missing:raise ValueError('Missing release files: '+', '.join(sorted(missing)))
    manifest={}
    metadata={'project':'Pocket Editor','version':version,'author':author_details(root),
              'distribution':'Python/Tkinter source','entry_point':'pocket_editor.py',
              'created_utc':datetime.now(timezone.utc).isoformat(),'file_count':len(files)}
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=output,prefix='.pocket-release-',delete=False) as stream:temporary=Path(stream.name)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for path in files:
                relative=path.relative_to(root).as_posix()
                data=path.read_bytes()
                if path.suffix=='.py':compile(data,str(path),'exec')
                manifest[relative]=hashlib.sha256(data).hexdigest()
                archive.writestr(name+'/'+relative,data)
            archive.writestr(name+'/RELEASE.json',json.dumps(metadata,indent=2)+'\n')
            archive.writestr(name+'/MANIFEST.sha256',''.join(digest+'  '+path+'\n' for path,digest in sorted(manifest.items())))
        with zipfile.ZipFile(temporary) as archive:
            damaged=archive.testzip()
            if damaged:raise ValueError('Release archive verification failed: '+damaged)
        os.replace(temporary,target);temporary=None
    finally:
        if temporary:temporary.unlink(missing_ok=True)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='utf-8')
    target.with_suffix('.release.json').write_text(json.dumps(dict(metadata,sha256=digest,archive=target.name),indent=2)+'\n',encoding='utf-8')
    return target,metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--version')
    args=parser.parse_args()
    archive,metadata=build_release(output=args.output,version=args.version)
    print('Release:',archive)
    print('Author:',metadata['author']['name'],metadata['author']['email'])
    print('Packaged source/document files:',metadata['file_count'])
    print('Size:',archive.stat().st_size,'bytes')

if __name__=='__main__':main()
