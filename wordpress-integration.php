<?php
/**
 * MI Assist — WordPress Integration
 * File: /wp-content/themes/your-theme/mi-assist-integration.php
 *
 * Add to functions.php:
 *   require_once get_template_directory() . '/mi-assist-integration.php';
 */

defined('ABSPATH') || exit;

// ── 1. REST endpoint: validate nonce for JWT exchange ──────

add_action('rest_api_init', function () {
    register_rest_route('mi-assist/v1', '/verify-nonce', [
        'methods'             => 'GET',
        'callback'            => 'mi_assist_verify_nonce',
        'permission_callback' => '__return_true',
    ]);
});

function mi_assist_verify_nonce(WP_REST_Request $request): WP_REST_Response {
    // Verify shared secret
    $secret = $request->get_header('X-MI-Secret');
    if ($secret !== MI_ASSIST_API_SECRET) {
        return new WP_REST_Response(['valid' => false, 'error' => 'bad_secret'], 401);
    }

    $nonce   = $request->get_param('nonce');
    $user_id = (int) $request->get_param('user_id');

    if (!$nonce || !$user_id) {
        return new WP_REST_Response(['valid' => false, 'error' => 'missing_params'], 400);
    }

    // Verify the nonce is valid AND belongs to the claimed user
    $valid = wp_verify_nonce($nonce, 'mi_assist_auth_' . $user_id);
    if (!$valid) {
        return new WP_REST_Response(['valid' => false, 'error' => 'invalid_nonce'], 401);
    }

    $user = get_userdata($user_id);
    if (!$user) {
        return new WP_REST_Response(['valid' => false, 'error' => 'user_not_found'], 404);
    }

    return new WP_REST_Response([
        'valid'   => true,
        'user_id' => $user_id,
        'email'   => $user->user_email,
    ], 200);
}


// ── 2. Inject MI Assist data into page ─────────────────────

add_action('wp_enqueue_scripts', function () {
    if (!is_page('mi-assist')) return;

    $user_id = get_current_user_id();
    $nonce   = $user_id ? wp_create_nonce('mi_assist_auth_' . $user_id) : '';

    wp_localize_script('mi-assist-chat', 'MI_ASSIST_CONFIG', [
        'api_url'    => MI_ASSIST_API_URL . '/v1',
        'wp_user_id' => $user_id,
        'email'      => $user_id ? wp_get_current_user()->user_email : '',
        'nonce'      => $nonce,
        'logged_in'  => (bool) $user_id,
    ]);
});


// ── 3. Redirect to login if not authenticated ───────────────

add_action('template_redirect', function () {
    if (is_page('mi-assist') && !is_user_logged_in()) {
        wp_redirect(wp_login_url(get_permalink()));
        exit;
    }
});


// ── 4. Constants (define in wp-config.php) ─────────────────
// Add these to wp-config.php:
//   define('MI_ASSIST_API_URL',    'https://yourdomain.com');
//   define('MI_ASSIST_API_SECRET', 'same-value-as-WP_API_SECRET-in-.env');
