<?php
// A non-namespaced helper loaded through require_once.
function legacy_customer_label(string $name): string
{
    return strtoupper($name);
}
