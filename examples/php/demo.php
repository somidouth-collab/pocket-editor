<?php
// Deliberately uneven spacing: try Extensions → Format PHP, then Undo.
namespace PocketDemo;
use PocketDemo\Models\Customer;
use PocketDemo\Services\Greeter as WelcomeService;
use function PocketDemo\Support\format_customer_label;
require_once __DIR__ . "/includes/helpers.php";
require_once __DIR__ . "/src/Models/Customer.php";
require_once __DIR__ . "/src/Services/Greeter.php";
$customer = new Customer("Adil", "adil@example.test");
$greeter = new WelcomeService();
echo $greeter->greetCustomer($customer), PHP_EOL;
echo format_customer_label($customer->displayName()), PHP_EOL;
// Completion practice: type $greeter-> then Ctrl+Space for greetCustomer.
// Type $customer-> for displayName, name and email from the imported file.
// Type WelcomeService:: for defaultGreeting, or format_customer_ for the use function alias.
