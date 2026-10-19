<?php
// Meridian Labs webmail settings (loaded by the Roundcube container from /var/roundcube/config).
// Users log in with their directory username (e.g. "skim"); the domain is added automatically.
$config['username_domain'] = 'meridian.test';
$config['product_name'] = 'Meridian Labs Webmail';

// Verify the mail server certificate against the lab's own certificate authority.
$tls = ['ssl' => ['cafile' => '/etc/meridian/ca.crt', 'verify_peer' => true, 'verify_peer_name' => true]];
$config['imap_conn_options'] = $tls;
$config['smtp_conn_options'] = $tls;
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
