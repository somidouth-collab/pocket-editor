<?php
// Completion exercises for user-defined PHP, inheritance, traits, enums and PHPDoc.
namespace PocketDemo;

use PocketDemo\Advanced\{State, UserRepository};
use PocketDemo\Models\Customer;

$repository = new UserRepository();
/** @var Customer[] $customers */
$customers = [];

// On a new PHP line, try each prefix then Ctrl+Space:
// $repository->find        => inherited findCustomer()
// $repository->lab         => trait label()
// $repository->ident       => interface implementation identifier()
// $repository->magic       => PHPDoc magicReport()
// $repository->findCustomer()->disp => return-type-aware displayName()
// UserRepository::TAB      => TABLE
// State::Re                => Ready enum case
// $customers[0]->disp       => PHPDoc array element displayName()
// str_rep                  => built-in str_replace()
