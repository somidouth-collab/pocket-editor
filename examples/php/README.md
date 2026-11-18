# PHP test project

Open `demo.php` with Pocket Editor. Extensions → Format PHP formats the buffer when needed, and Edit → Undo restores a formatting change. It may already be formatted if you previously saved it. Save only when you want to update the sample file.

For completion exercises, add a new PHP line after the existing code (not inside a comment), type a prefix, then press Ctrl+Space:

| Prefix | Expected completion | Source |
| --- | --- | --- |
| `$customer->disp` | `displayName` | `use PocketDemo\Models\Customer`, defined in `src/Models/Customer.php` |
| `$greeter->gre` | `greetCustomer` | `WelcomeService` alias for `src/Services/Greeter.php` |
| `WelcomeService::defaultG` | `defaultGreeting` | Static method on the imported alias |
| `format_customer_l` | `format_customer_label` | `use function` from `includes/helpers.php` |

The first request indexes this small Composer project. Select with arrows and insert with Enter/Tab, or click a suggestion. Escape closes the popup. Function/method completions can insert parentheses and parameter placeholders. Tab/Shift+Tab moves between parameters; Escape ends placeholder navigation.

`grouped.php` tests grouped class imports. `includes/legacy.php` supplies a global helper for require/include completion tests. `composer.json` supplies the PSR-4 namespace map without requiring Composer installation. The valid sample program uses explicit `require_once` statements for execution; running it requires a separate PHP interpreter.

To see changed-file tracking, edit and save an imported class, then request completion in `demo.php`. Use Extensions → Reindex PHP Project after filesystem changes made outside the editor.

## Your PHP application

Use Extensions → Open Project to select your application's folder. Open its PHP document and choose Extensions → PHP Completion Settings. Set the target PHP version and add extra source/library or framework-stub folders that live outside the project. Normal project files, Composer namespaces and installed vendor dependencies are discovered by the language server. No PHP application code is executed by completion.

## Advanced exercises

Open `advanced_completion.php`. Its comments show inheritance, traits, interface implementations, enum cases, PHPDoc magic methods, typed return chaining and built-in function examples. The actual declarations are in `src/Advanced/UserRepository.php`. Suggestions can be requested on a new PHP line with Ctrl+Space. To test nullable members inside a typed function, use `function example(?Customer $customer) { $customer?->disp` and request completion at the end (incomplete code is allowed while editing, but must be fixed before formatting/executing).

The popup shows symbol type and available documentation, with scrolling and Page Up/Page Down. Default automatic delay is 120 ms; tune it or disable automatic suggestions in PHP Completion Settings. Completion runs only for PHP-family files, preserving normal keys in other documents. Dynamic framework methods require useful type declarations, PHPDoc or stubs; not every runtime-generated API can be inferred.
