<?php
namespace PocketDemo;

use PocketDemo\{Models\Customer, Services\Greeter as WelcomeService};

$customer = new Customer('Adil', 'adil@example.test');
$greeter = new WelcomeService();
echo $greeter->greetCustomer($customer);
