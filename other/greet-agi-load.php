#!/usr/bin/php -q
<?php
// 1. Setup Manual AGI Communication
$stdin = fopen('php://stdin', 'r');
$stdout = fopen('php://stdout', 'w');

function agi_exec($cmd) {
    global $stdin, $stdout;
    fputs($stdout, $cmd . "\n");
    fflush($stdout);
    return fgets($stdin);
}

// 2. Capture arguments
$unique_id   = isset($argv[1]) ? $argv[1] : '';

// 3. Load Vicidial Config
$config = file_get_contents('/etc/astguiclient.conf');
preg_match('/VARDB_server\s*=>\s*(.*)/', $config, $m); $server = trim($m[1]);
preg_match('/VARDB_user\s*=>\s*(.*)/', $config, $m);   $user = trim($m[1]);
preg_match('/VARDB_pass\s*=>\s*(.*)/', $config, $m);   $pass = trim($m[1]);
preg_match('/VARDB_database\s*=>\s*(.*)/', $config, $m); $db = trim($m[1]);

$link = mysqli_connect($server, $user, $pass, $db);

// 4. Fetch Data    
$first_name = "Guest";
$lead_id = "";

if (!empty($unique_id)) {
    $query1 = "SELECT lead_id FROM vicidial_auto_calls WHERE callerid = '" . mysqli_real_escape_string($link, $unique_id) . "' LIMIT 1";
    $result1 = mysqli_query($link, $query1);
    if ($result1 && mysqli_num_rows($result1) > 0) {
        $row1 = mysqli_fetch_assoc($result1);
        $lead_id = trim($row1['lead_id']);
    }

    $query = "SELECT first_name FROM vicidial_list WHERE lead_id = '" . mysqli_real_escape_string($link, $lead_id) . "' LIMIT 1";
    $result = mysqli_query($link, $query);
    if ($result && mysqli_num_rows($result) > 0) {
        $row = mysqli_fetch_assoc($result);
        $first_name = trim($row['first_name']);
    }
}

// 5. Create AI Payload (Base64-encoded JSON)
$payload_data = [
    "fn" => $first_name,
    "lead_id" => $lead_id,
    "unique_id" => $unique_id
];
$payload_json = json_encode($payload_data);
$ai_payload = base64_encode($payload_json);

// 6. Push to FastAPI (Change IP to your LiveKit Server)
$api_url = 'http://192.168.1.61:9001/receive-data'; 
$post_data = [
    "unique_id" => (string)$unique_id,
    "field_1"   => (string)$first_name,
    "field_2"   => "lead_" . $lead_id,
    "field_3"   => "active"
];

$ch = curl_init($api_url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($post_data));
curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
curl_setopt($ch, CURLOPT_TIMEOUT, 2);   
curl_exec($ch);
curl_close($ch);

// 7. Send Variables back to Asterisk (INCLUDING AI_PAYLOAD)
agi_exec("SET VARIABLE AI_UNIQUEID \"$unique_id\"");
agi_exec("SET VARIABLE AI_NAME \"$first_name\"");
agi_exec("SET VARIABLE AI_PAYLOAD \"$ai_payload\"");
agi_exec("VERBOSE \"AGI: Pushed data for $unique_id with name $first_name\" 1");

fclose($stdin);
fclose($stdout);
mysqli_close($link);
?>