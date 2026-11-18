<?php
namespace PocketDemo\Models;

final class Customer
{
    public function __construct(public string $name, public string $email) {}

    public function displayName(): string
    {
        return $this->name;
    }
}
