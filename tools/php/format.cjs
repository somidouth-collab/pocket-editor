// PHP parsing/printing stays in a maintained formatter, never regex rewriting.
const fs = require('node:fs');
const prettier = require('prettier');
(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const php = await import('@prettier/plugin-php');
  const config = await prettier.resolveConfig(input.filename, { editorconfig: true });
  const result = await prettier.formatWithCursor(input.source, {
    printWidth: input.width, tabWidth: input.tabs, phpVersion: input.phpVersion,
    ...config, plugins: [php.default || php], parser: 'php', filepath: input.filename,
    endOfLine: 'lf', cursorOffset: [...input.source].slice(0, input.cursor).join('').length,
  });
  if (result.cursorOffset >= 0) result.cursorOffset = [...result.formatted.slice(0, result.cursorOffset)].length;
  process.stdout.write(JSON.stringify(result));
})().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
