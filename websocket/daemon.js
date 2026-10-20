var config = require('./config');
var set = require('simplesets').Set;
var queue = require('qu');
var WebSocketServer = require('ws').Server;
// Browsers only ever send the two tiny subscription commands; the cap keeps a stranger from making
// this process buffer up to the library default of 100 MiB. Posts come from Django on loopback and
// may carry a compile log, so that side keeps a generous cap.
var wss_receiver = new WebSocketServer({host: config.get_host, port: config.get_port,
                                        maxPayload: config.max_receive_payload || 64 * 1024});
var wss_sender = new WebSocketServer({host: config.post_host, port: config.post_port,
                                      maxPayload: config.max_post_payload || 32 * 1024 * 1024});
var messages = new queue();
var followers = new set();
var pollers = new set();
var max_queue = config.max_queue || 50;
var long_poll_timeout = config.long_poll_timeout || 60000;
var heartbeat_interval = config.heartbeat_interval || 25000;
var message_id = Date.now();

if (typeof String.prototype.startsWith != 'function') {
    String.prototype.startsWith = function (str){
        return this.slice(0, str.length) == str;
    };
}

// A request is only ever a JSON object with a string command. Anything else used to reach
// request.command.replace() and throw out of the socket handler, which killed the whole process.
function parse_request(raw) {
    var request;
    try {
        request = JSON.parse(raw);
    } catch (err) {
        return {error: {status: 'error', code: 'syntax-error', message: err.message}};
    }
    if (request === null || typeof request != 'object' || Array.isArray(request) ||
            typeof request.command != 'string')
        return {error: {status: 'error', code: 'bad-command', message: 'bad command'}};
    request.command = request.command.replace(/-/g, '_');
    return {request: request};
}

// `in` would also find Object.prototype members such as __defineGetter__.
function has_command(commands, name) {
    return Object.prototype.hasOwnProperty.call(commands, name);
}

function safe_send(socket, data) {
    try {
        socket.send(JSON.stringify(data));
    } catch (err) {
        // The socket is going away; nothing useful to do.
    }
}

messages.catch_up = function (client) {
    this.each(function (message) {
        if (message.id > client.last_msg)
            client.got_message(message);
    });
};

messages.post = function (channel, message) {
    message = {
        id: ++message_id,
        channel: channel,
        message: message
    };
    this.push(message);
    if (this.length > max_queue)
        this.shift();
    followers.each(function (client) {
        client.got_message(message);
    });
    pollers.each(function (request) {
        request.got_message(message);
    });
    return message.id;
};

messages.last = function () {
    return this.tail().id;
};

wss_receiver.on('connection', function (socket) {
    socket.channel = null;
    socket.last_msg = 0;
    socket.is_alive = true;
    socket.filter = Object.create(null);

    socket.on('pong', function () {
        socket.is_alive = true;
    });

    var commands = {
        start_msg: function (request) {
            socket.last_msg = request.start;
        },
        set_filter: function (request) {
            var filter = Object.create(null);
            if (Array.isArray(request.filter) && request.filter.length > 0 &&
                request.filter.every(function (channel, index, array) {
                    if (typeof channel != 'string')
                        return false;
                    filter[channel] = true;
                    return true;
            })) {
                socket.filter = filter;
                followers.add(socket);
                messages.catch_up(socket);
            } else {
                safe_send(socket, {
                    status: 'error',
                    code: 'invalid-filter',
                    message: 'invalid filter: ' + request.filter
                });
            }
        },
    };

    socket.got_message = function (message) {
        // One broken follower must not keep the message from the rest.
        if (message.channel in socket.filter)
            safe_send(socket, message);
        socket.last_msg = message.id;
    };

    socket.on('message', function (raw) {
        try {
            var parsed = parse_request(raw);
            if (parsed.error) {
                safe_send(socket, parsed.error);
                return;
            }
            var request = parsed.request;
            if (has_command(commands, request.command))
                commands[request.command](request);
            else
                safe_send(socket, {
                    status: 'error',
                    code: 'bad-command',
                    message: 'bad command: ' + request.command
                });
        } catch (err) {
            safe_send(socket, {status: 'error', code: 'internal-error'});
        }
    });

    // ws emits 'error' for any malformed frame (bad opcode, RSV bits, invalid UTF-8, oversized
    // message) and then closes the socket. With no listener that event throws and kills the process.
    socket.on('error', function () {
        followers.remove(socket);
    });

    socket.on('close', function(code, message) {
        followers.remove(socket);
    });
});

// Without traffic of its own a listening socket looks idle to whatever sits in
// front of this daemon, and the edge proxy drops it after a couple of minutes.
// Pinging keeps it open and, because a browser answers automatically, also
// reveals clients that went away without closing.
setInterval(function () {
    wss_receiver.clients.forEach(function (socket) {
        if (socket.is_alive === false) {
            socket.terminate();
            return;
        }
        socket.is_alive = false;
        try {
            socket.ping();
        } catch (err) {
            socket.terminate();
        }
    });
}, heartbeat_interval);

wss_sender.on('connection', function (socket) {
    var commands = {
        post: function (request) {
            if (typeof request.channel != 'string')
                return {
                    status: 'error',
                    code: 'invalid-channel'
                };
            return {
                status: 'success',
                id: messages.post(request.channel, request.message)
            };
        },
        last_msg: function (request) {
            return {
                status: 'success',
                id: message_id,
            };
        }
    };
    socket.on('message', function (raw) {
        try {
            var parsed = parse_request(raw);
            if (parsed.error) {
                safe_send(socket, parsed.error);
                return;
            }
            var request = parsed.request;
            if (has_command(commands, request.command))
                safe_send(socket, commands[request.command](request));
            else
                safe_send(socket, {
                    status: 'error',
                    code: 'bad-command',
                    message: 'bad command: ' + request.command
                });
        } catch (err) {
            safe_send(socket, {status: 'error', code: 'internal-error'});
        }
    });
    socket.on('error', function () {});
});

var url = require('url');
require('http').createServer(function (req, res) {
    try {
        var parts = url.parse(req.url, true);

        if (!parts.pathname || !parts.pathname.startsWith('/channels/')) {
            res.writeHead(404, {'Content-Type': 'text/plain'});
            res.end('404 Not Found');
            return;
        }

        var channels = parts.pathname.slice(10).split('|');
        if (channels.length == 1 && !channels[0].length) {
            res.writeHead(400, {'Content-Type': 'text/plain'});
            res.end('400 Bad Request');
            return;
        }

        req.channels = Object.create(null);
        req.last_msg = parseInt(parts.query.last);
        if (isNaN(req.last_msg)) req.last_msg = 0;

        channels.forEach(function (channel) {
            req.channels[channel] = true;
        });

        req.on('close', function () {
            pollers.remove(req);
        });

        req.got_message = function (message) {
            if (message.channel in req.channels) {
                pollers.remove(req);
                if (res.writableEnded || res.destroyed)
                    return true;
                res.writeHead(200, {'Content-Type': 'application/json'});
                res.end(JSON.stringify(message));
                return true;
            }
            return false;
        };
        var got = false;
        messages.each(function (message) {
            if (!got && message.id > req.last_msg)
                got = req.got_message(message);
        });
        if (!got) {
            pollers.add(req);
            res.setTimeout(long_poll_timeout, function () {
                pollers.remove(req);
                if (res.writableEnded || res.destroyed)
                    return;
                res.writeHead(504, {'Content-Type': 'application/json'});
                res.end('{"error": "timeout"}');
            });
        }
    } catch (err) {
        pollers.remove(req);
        try {
            if (!res.headersSent)
                res.writeHead(500, {'Content-Type': 'text/plain'});
            res.end();
        } catch (ignored) {
            // The client is gone.
        }
    }
}).listen(config.http_port, config.http_host);
