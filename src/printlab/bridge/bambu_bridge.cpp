/*
 * bambu_bridge.cpp — Thin C wrapper around libbambu_networking.dylib
 *
 * The Bambu networking dylib exports C++ functions that use std::string
 * parameters, which Python ctypes cannot call directly. This bridge
 * translates between C strings (const char*) and std::string.
 *
 * Compile:
 *   clang++ -shared -std=c++17 -o bambu_bridge.dylib bambu_bridge.cpp
 */

#include <cstring>
#include <dlfcn.h>
#include <string>

// --- Function pointer types matching the dylib's exported symbols ---

using fn_create_agent    = void* (*)(std::string);          // log_dir -> agent*
using fn_destroy_agent   = int   (*)(void*);                // agent
using fn_init_log        = int   (*)(void*);                // agent
using fn_set_config_dir  = int   (*)(void*, std::string);   // agent, config_dir
using fn_set_cert_file   = int   (*)(void*, std::string, std::string); // agent, folder, filename
using fn_set_country_code= int   (*)(void*, std::string);   // agent, country_code
using fn_start           = int   (*)(void*);                // agent
using fn_connect_server  = int   (*)(void*);                // agent
using fn_is_user_login   = bool  (*)(void*);                // agent
using fn_build_login_info= std::string (*)(void*);          // agent -> json string

// --- Resolved function pointers ---

static void* g_dylib         = nullptr;
static void* g_agent         = nullptr;

static fn_create_agent     p_create_agent     = nullptr;
static fn_destroy_agent    p_destroy_agent    = nullptr;
static fn_init_log         p_init_log         = nullptr;
static fn_set_config_dir   p_set_config_dir   = nullptr;
static fn_set_cert_file    p_set_cert_file    = nullptr;
static fn_set_country_code p_set_country_code = nullptr;
static fn_start            p_start            = nullptr;
static fn_connect_server   p_connect_server   = nullptr;
static fn_is_user_login    p_is_user_login    = nullptr;
static fn_build_login_info p_build_login_info = nullptr;

// Thread-local buffer for returning strings to Python
static thread_local std::string g_return_buf;

// --- Pure-C interface ---

extern "C" {

/**
 * Load the dylib from the given path and resolve all symbols.
 * Returns 0 on success, -1 on failure.
 */
int bridge_load(const char* dylib_path) {
    if (g_dylib) return 0; // already loaded

    g_dylib = dlopen(dylib_path, RTLD_NOW | RTLD_LOCAL);
    if (!g_dylib) return -1;

    #define RESOLVE(name) \
        p_##name = reinterpret_cast<fn_##name>(dlsym(g_dylib, "bambu_network_" #name)); \
        if (!p_##name) return -1;

    RESOLVE(create_agent)
    RESOLVE(destroy_agent)
    RESOLVE(init_log)
    RESOLVE(set_config_dir)
    RESOLVE(set_cert_file)
    RESOLVE(set_country_code)
    RESOLVE(start)
    RESOLVE(connect_server)
    RESOLVE(is_user_login)
    RESOLVE(build_login_info)

    #undef RESOLVE
    return 0;
}

/**
 * Create an agent and initialize it with the given config/cert paths.
 * config_dir: e.g. "~/Library/Application Support/BambuStudio/"
 * cert_dir:   e.g. "/Applications/BambuStudio.app/Contents/Resources/cert"
 * Returns 0 on success, -1 on failure.
 */
int bridge_init(const char* config_dir, const char* cert_dir) {
    if (!p_create_agent) return -1;
    if (g_agent) return 0; // already initialized

    g_agent = p_create_agent(std::string(config_dir));
    if (!g_agent) return -1;

    p_init_log(g_agent);
    p_set_config_dir(g_agent, std::string(config_dir));
    p_set_cert_file(g_agent, std::string(cert_dir), std::string("slicer_base64.cer"));
    p_set_country_code(g_agent, std::string("US"));
    p_start(g_agent);

    return 0;
}

/**
 * Check if a user is currently logged in.
 * Returns 1 if logged in, 0 if not, -1 on error.
 */
int bridge_is_user_login(void) {
    if (!g_agent || !p_is_user_login) return -1;
    return p_is_user_login(g_agent) ? 1 : 0;
}

/**
 * Build login info JSON string (contains token/user details).
 * Returns pointer to a thread-local buffer, or NULL on failure.
 * The returned string is valid until the next call to this function
 * from the same thread.
 */
const char* bridge_build_login_info(void) {
    if (!g_agent || !p_build_login_info) return nullptr;

    g_return_buf = p_build_login_info(g_agent);
    if (g_return_buf.empty()) return nullptr;

    return g_return_buf.c_str();
}

/**
 * Clean up: destroy agent and close dylib.
 */
void bridge_cleanup(void) {
    if (g_agent && p_destroy_agent) {
        p_destroy_agent(g_agent);
        g_agent = nullptr;
    }
    if (g_dylib) {
        dlclose(g_dylib);
        g_dylib = nullptr;
    }
    // Reset all pointers
    p_create_agent     = nullptr;
    p_destroy_agent    = nullptr;
    p_init_log         = nullptr;
    p_set_config_dir   = nullptr;
    p_set_cert_file    = nullptr;
    p_set_country_code = nullptr;
    p_start            = nullptr;
    p_connect_server   = nullptr;
    p_is_user_login    = nullptr;
    p_build_login_info = nullptr;
}

} // extern "C"
