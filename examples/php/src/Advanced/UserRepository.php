<?php
namespace PocketDemo\Advanced;

use PocketDemo\Models\Customer;

interface Identifiable
{
    public function identifier(): string;
}

trait HasLabel
{
    public function label(): string
    {
        return 'Customer repository';
    }
}

enum State: string
{
    case Ready = 'ready';
    case Pending = 'pending';
}

class BaseRepository
{
    public function findCustomer(): Customer
    {
        return new Customer('Ada', 'ada@example.test');
    }
}

/** @method string magicReport() */
final class UserRepository extends BaseRepository implements Identifiable
{
    use HasLabel;

    public const TABLE = 'customers';

    public function identifier(): string
    {
        return self::TABLE;
    }
}
