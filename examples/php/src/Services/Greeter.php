<?php
namespace PocketDemo\Services;

use PocketDemo\Models\Customer;

final class Greeter
{
    public function greetCustomer(Customer $customer): string
    {
        return 'Hello, ' . $customer->displayName();
    }

    public static function defaultGreeting(): string
    {
        return 'Welcome to Pocket Editor';
    }
}
