'use strict';
// Local JSON-lines bridge. Source is parsed only; project code/config is never executed.
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const prettier = require('prettier');
const htmlApi = require('vscode-html-languageservice');
const html = htmlApi.getLanguageService();
const css = require('vscode-css-languageservice');
const {TextDocument} = require('vscode-languageserver-textdocument');
const ts = require('typescript');
const styles = {css: css.getCSSLanguageService(), scss: css.getSCSSLanguageService(), less: css.getLESSLanguageService()};
const parsers = {html:'html', css:'css', scss:'scss', less:'less', javascript:'babel', javascriptreact:'babel', typescript:'typescript', typescriptreact:'typescript'};
let virtual = new Map(), project = '', service, revision = 0;
const normalizeFile = file => {
  const value = path.resolve(file).replace(/\\/g, '/');
  return ts.sys.useCaseSensitiveFileNames ? value : value.toLowerCase();
};
const libRoot = path.dirname(require.resolve('typescript'));
const within = (p, root) => { const relative = path.relative(root, p); return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative)); };
function allowed(file) {
  try {
    const real = fs.realpathSync(file);
    const size = fs.statSync(real).size;
    if (within(real, libRoot) && real.endsWith('.d.ts')) return size <= 10000000;
    return within(real, project) && /\.(?:[cm]?[jt]sx?|json)$/i.test(real) && size <= 1000000;
  } catch { return false; }
}
function jsService(request, file, source) {
  file = normalizeFile(file);
  const root = normalizeFile(request.root || path.dirname(file));
  if (root !== project) { if (service) service.dispose(); service = undefined; project = root; }
  virtual = new Map(); revision++;
  for (const doc of (request.documents || []).slice(0, 32)) {
    const name = normalizeFile(doc.path);
    if (/\.[cm]?[jt]sx?$/i.test(name) && within(name, project) && typeof doc.source === 'string' && doc.source.length <= 300000) virtual.set(name, doc.source);
  }
  virtual.set(file, source);
  if (!service) {
    const options = {allowJs:true, checkJs:false, jsx:ts.JsxEmit.Preserve, target:ts.ScriptTarget.ESNext, module:ts.ModuleKind.ESNext, moduleResolution:ts.ModuleResolutionKind.Bundler, allowNonTsExtensions:true};
    const read = file => { file = normalizeFile(file); return virtual.has(file) ? virtual.get(file) : allowed(file) ? fs.readFileSync(file, 'utf8') : undefined; };
    service = ts.createLanguageService({getCompilationSettings:()=>options,
      getScriptFileNames:()=>[...virtual.keys()], getScriptVersion:file=>virtual.has(normalizeFile(file))?String(revision):allowed(file)?String(fs.statSync(file).mtimeMs):'0',
      getScriptSnapshot:file=>{const text=read(file);return text===undefined?undefined:ts.ScriptSnapshot.fromString(text);},
      getCurrentDirectory:()=>project, getDefaultLibFileName:opts=>ts.getDefaultLibFilePath(opts),
      fileExists:file=>virtual.has(normalizeFile(file))||allowed(file), readFile:read,
      directoryExists:dir=>within(path.resolve(dir),project)||within(path.resolve(dir),libRoot),
      useCaseSensitiveFileNames:()=>ts.sys.useCaseSensitiveFileNames});
  }
  return service;
}
function embedded(doc, offset) {
  const tree = html.parseHTMLDocument(doc);
  for(let node=tree.findNodeAt(offset);node;node=node.parent) {
    const tag = (node.tag || '').toLowerCase();
    if (['style','script'].includes(tag) && node.startTagEnd <= offset && offset <= (node.endTagStart ?? node.end)) {
      const source=doc.getText(),start=node.startTagEnd,end=node.endTagStart ?? node.end;
      return {language:tag==='style'?'css':'javascript',source:source.slice(0,start).replace(/[^\r\n]/g,' ')+source.slice(start,end)+source.slice(end).replace(/[^\r\n]/g,' ')};
    }
  }
  return null;
}
function joinTagBrackets(result) {
  // Only remove line breaks INSIDE a tag. Inline-element whitespace, comments,
  // preformatted text and script/style contents retain Prettier's semantics.
  const scanner = html.createScanner(result.formatted), edits = [];
  let previous = null;
  for (let token = scanner.scan(); token !== htmlApi.TokenType.EOS; token = scanner.scan()) {
    if ((token === htmlApi.TokenType.StartTagClose || token === htmlApi.TokenType.EndTagClose) && previous?.token === htmlApi.TokenType.Whitespace) {
      const gap = result.formatted.slice(previous.start, previous.end);
      if (/[\r\n]/.test(gap)) edits.push(previous);
    }
    previous = {token, start: scanner.getTokenOffset(), end: scanner.getTokenEnd()};
  }
  for (const {start,end} of edits.reverse()) {
    result.formatted = result.formatted.slice(0,start) + result.formatted.slice(end);
    if (result.cursorOffset >= end) result.cursorOffset -= end-start;
    else if (result.cursorOffset > start) result.cursorOffset = start;
  }
  return result;
}
async function execute(request) {
  let {source, language, operation} = request;
  if(typeof source!=='string'||source.length>300000||!parsers[language]) throw Error('Unsupported or oversized web document.');
  let file = normalizeFile(request.path || 'untitled.js');
  let offset = Math.max(0,Math.min(source.length,request.offset || 0));
  if(operation==='format') {
    const result = await prettier.formatWithCursor(source,{parser:parsers[language],cursorOffset:offset,
      tabWidth:request.indent===4?4:2,printWidth:Math.max(60,Math.min(200,request.width||100)),
      useTabs:!!request.tabs,singleQuote:!!request.singleQuote,semi:request.semi!==false,
      bracketSameLine:language==='html',endOfLine:'lf',embeddedLanguageFormatting:'auto'});
    return language==='html' ? joinTagBrackets(result) : result;
  }
  let doc=TextDocument.create(pathToFileURL(file).href,language,1,source);
  if(language==='html' && operation==='complete') {
    const inner=embedded(doc,offset);
    if(inner) {language=inner.language;source=inner.source;file+='.'+(language==='css'?'css':'js');doc=TextDocument.create(pathToFileURL(file).href,language,1,source);}
  }
  const position=doc.positionAt(offset);
  if(operation==='complete') {
    let items;
    if(language==='html') items=html.doComplete(doc,position,html.parseHTMLDocument(doc)).items;
    else if(styles[language]) items=styles[language].doComplete(doc,position,styles[language].parseStylesheet(doc)).items;
    else {
      const ls=jsService(request,file,source);
      const result=ls.getCompletionsAtPosition(file,offset,{includeCompletionsForModuleExports:false,includeCompletionsWithInsertText:true});
      items=(result?.entries || []).map(item=>{
        const span=item.replacementSpan || result.optionalReplacementSpan;
        const prefix=source.slice(0,offset).match(/[\w$]*$/)[0];
        const start=span?span.start:offset-prefix.length,end=span?span.start+span.length:offset;
        return {label:item.name,detail:item.kind,sortText:item.sortText,insertTextFormat:item.isSnippet?2:1,
          textEdit:{range:{start:doc.positionAt(start),end:doc.positionAt(end)},newText:item.insertText||item.name}};
      });
    }
    const prefix=source.slice(0,offset).match(/[\w$-]*$/)[0].toLowerCase();
    return items.filter(item=>!prefix || String(item.filterText || item.label).toLowerCase().startsWith(prefix))
      .sort((a,b)=>String(a.sortText||a.label).localeCompare(String(b.sortText||b.label))).slice(0,120);
  }
  if(operation==='check') {
    let issues=[];
    if(styles[language]) issues=styles[language].doValidation(doc,styles[language].parseStylesheet(doc));
    else if(language!=='html') {
      const ls=jsService(request,file,source);
      issues=ls.getSyntacticDiagnostics(file).map(d=>({range:{start:doc.positionAt(d.start||0),end:doc.positionAt((d.start||0)+(d.length||1))},severity:1,message:ts.flattenDiagnosticMessageText(d.messageText,'\n')}));
    }
    // Prettier's parser adds structural HTML checking and rejects invalid syntax.
    try {await prettier.format(source,{parser:parsers[language]});}
    catch(error) {const pos=error.loc?.start || error.loc || {line:1,column:1};issues.push({range:{start:{line:Math.max(0,(pos.line||1)-1),character:Math.max(0,(pos.column||1)-1)},end:{line:Math.max(0,(pos.line||1)-1),character:pos.column||1}},severity:1,message:String(error.message).split('\n')[0]});}
    return issues.slice(0,100);
  }
  throw Error('Unknown web operation.');
}
(async()=>{for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
  try {const request=JSON.parse(line);process.stdout.write(JSON.stringify({result:await execute(request)})+'\n');}
  catch(error){process.stdout.write(JSON.stringify({error:String(error.message || error)})+'\n');}
}})();
