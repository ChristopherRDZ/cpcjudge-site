function EventReceiver(websocket, poller, channels, last_msg, onmessage) {
    this.websocket_path = websocket;
    this.channels = channels;
    this.last_msg = last_msg;
    this.poller_base = poller;
    this.poller_path = poller + channels.join('|');
    if (onmessage)
        this.onmessage = onmessage;
    var receiver = this;

    // An idle WebSocket is dropped by the edge proxy after about two minutes
    // with code 1006, so the connection has to be rebuilt rather than leaving
    // the page stale until the user reloads it by hand.
    var RECONNECT_BASE = 1000;  // delay before the first retry, in ms
    var RECONNECT_CAP = 30000;  // ceiling for the exponential backoff
    var WARN_AFTER = 3;         // consecutive failures before telling the user
    var POLL_AFTER = 6;         // consecutive failures before giving up on WebSocket
    var OPEN_TIMEOUT = 5000;    // 2s was not enough to open over a mobile network

    this.websocket = null;
    this.onwsclose = null;
    this.onwsopen = null;
    this.polling = false;
    this.stopped = false;
    this.ever_opened = false;
    this.failures = 0;

    var reconnect_timer = null;

    function recovered() {
        if (receiver.onwsopen !== null)
            receiver.onwsopen();
    }

    function init_poll() {
        if (receiver.polling)
            return;
        receiver.polling = true;
        var announced = false;

        function long_poll() {
            if (receiver.stopped)
                return;
            $.ajax({
                url: receiver.poller_path,
                data: {last: receiver.last_msg},
                success: function (data, status, jqXHR) {
                    if (!announced) {
                        announced = true;
                        recovered();
                    }
                    receiver.onmessage(data.message);
                    receiver.last_msg = data.id;
                    long_poll();
                },
                error: function (jqXHR, status, error) {
                    if (jqXHR.status == 504) {
                        if (!announced) {
                            announced = true;
                            recovered();
                        }
                        long_poll();
                    } else {
                        console.log('Long poll failure: ' + status);
                        console.log(jqXHR);
                        setTimeout(long_poll, 2000);
                    }
                },
                dataType: "json"
            });
        }

        long_poll();
    }

    function reconnect_delay() {
        var ceiling = Math.min(RECONNECT_BASE * Math.pow(2, receiver.failures - 1), RECONNECT_CAP);
        return ceiling / 2 + Math.random() * ceiling / 2;
    }

    function schedule_reconnect() {
        if (receiver.stopped || receiver.polling || reconnect_timer !== null)
            return;
        // If a WebSocket never worked here at all, the transport is probably
        // blocked, so fall back to long polling instead of retrying forever.
        if (!receiver.ever_opened || receiver.failures >= POLL_AFTER) {
            init_poll();
            return;
        }
        reconnect_timer = setTimeout(function () {
            reconnect_timer = null;
            connect();
        }, reconnect_delay());
    }

    function connect() {
        if (receiver.stopped || receiver.polling)
            return;

        var socket, done = false, timeout = null;
        try {
            socket = new WebSocket(receiver.websocket_path);
        } catch (err) {
            receiver.failures++;
            schedule_reconnect();
            return;
        }
        receiver.websocket = socket;

        function detach() {
            done = true;
            if (timeout !== null) {
                clearTimeout(timeout);
                timeout = null;
            }
            if (receiver.websocket === socket)
                receiver.websocket = null;
        }

        function fail(event) {
            if (done)
                return;
            detach();
            receiver.failures++;
            if (receiver.failures >= WARN_AFTER && receiver.onwsclose !== null)
                receiver.onwsclose(event);
            schedule_reconnect();
        }

        timeout = setTimeout(function () {
            socket.close();
            fail({code: 1006, reason: 'open timed out'});
        }, OPEN_TIMEOUT);

        socket.onopen = function () {
            if (timeout !== null) {
                clearTimeout(timeout);
                timeout = null;
            }
            var was_down = receiver.failures > 0;
            receiver.ever_opened = true;
            receiver.failures = 0;
            // The start message is read off the receiver rather than the
            // closure, so a reconnection replays whatever arrived while it was
            // down instead of starting over from page load.
            this.send(JSON.stringify({
                command: 'start-msg',
                start: receiver.last_msg
            }));
            this.send(JSON.stringify({
                command: 'set-filter',
                filter: receiver.channels
            }));
            if (was_down)
                recovered();
        };

        socket.onmessage = function (event) {
            var data = JSON.parse(event.data);
            receiver.onmessage(data.message);
            receiver.last_msg = data.id;
        };

        socket.onclose = function (event) {
            // 1000 is a clean shutdown, 1001 means the page is going away.
            if (receiver.stopped || event.code == 1000 || event.code == 1001) {
                detach();
                return;
            }
            fail(event);
        };
    }

    function reconnect_now() {
        if (receiver.stopped || receiver.polling || receiver.websocket !== null)
            return;
        if (reconnect_timer !== null) {
            clearTimeout(reconnect_timer);
            reconnect_timer = null;
        }
        receiver.failures = 0;
        connect();
    }

    this.stop = function () {
        receiver.stopped = true;
        if (reconnect_timer !== null) {
            clearTimeout(reconnect_timer);
            reconnect_timer = null;
        }
        if (receiver.websocket !== null)
            receiver.websocket.close(1000);
    };

    if (window.WebSocket) {
        connect();
        // Returning to a backgrounded tab or regaining connectivity is exactly
        // when the page is most likely to be showing stale data.
        $(window).on('dmoj:window-visible', reconnect_now);
        if (window.addEventListener)
            window.addEventListener('online', reconnect_now);
    } else {
        init_poll();
    }
}
