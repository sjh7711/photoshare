self.addEventListener('push', function(event) {
    const data = event.data.json();
    let options = {};

    if (data.tag === 'photo_upload') {
        options = {
            head: data.head,
            body: data.body,
            icon: data.icon,
            badge: data.badge,
            image: data.image,
            tag: data.tag,
            data: {
                url: data.url
            }
        }
    } else if (data.tag === 'comment') {
        options = {
            head: data.head,
            body: data.body,
            icon: data.icon,
            badge: data.badge,
            tag: data.tag, 
            data: {
                url: data.url
            }
        };
    }
    event.waitUntil(
        self.registration.showNotification(data.head, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    event.waitUntil(
        clients.openWindow(event.notification.data.url)
    );
});