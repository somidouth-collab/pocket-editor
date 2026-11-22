<?php

final class HomeModel
{
    public function page(): array
    {
        return [
            'title' => 'FusionPHP editor example',
            'showLinks' => true,
            'links' => [
                ['title' => 'Home', 'url' => '/'],
                ['title' => 'Templates', 'url' => '/templates'],
            ],
        ];
    }
}
