// Copy to websocket/config.js and adapt locally. Do not commit secrets.
module.exports = {
    get_host: '127.0.0.1',
    get_port: 15100,
    post_host: '127.0.0.1',
    post_port: 15101,
    http_host: '127.0.0.1',
    http_port: 15102,
    long_poll_timeout: 29000,
    // Largest message accepted, in bytes: browsers only send two small subscription
    // commands; Django's posts may carry a compile log.
    max_receive_payload: 64 * 1024,
    max_post_payload: 32 * 1024 * 1024,
};
