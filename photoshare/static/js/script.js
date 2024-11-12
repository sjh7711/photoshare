function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

const publicVapidKey = 'BDjtH-LpOGJMMtWtRhmfpoaymsuY2Q8K1s5mZ5hQyzDEQko23ZDqHHoO8UHRYRy1Pa0O6YlbWbuszppIxkS2KbM';

async function sendRequest(url, data) {
    const csrftoken = getCookie('csrftoken');
    
    try {
        const response = await fetch(url, {
            method: 'POST',
            body: JSON.stringify(data),
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            credentials: 'same-origin'  // 이 옵션을 추가하여 쿠키를 포함시킵니다
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Request failed:', error);
        throw error;
    }
}

if ('serviceWorker' in navigator) {
    const blockSubscribed = localStorage.getItem('blockSubscribed');
    if (blockSubscribed !== 'true') {
        const device_id = localStorage.getItem('device_id');
        if (device_id) {
            sendRequest('/check_device_id/', { device_id: device_id })
                .then(data => {
                    if (!data.registered) {
                        run();
                    }
                })
                .catch(error => console.error('Error checking device ID:', error));
        } else {
            run();
        }
    }
}

async function run() {
    try {
        const registration = await navigator.serviceWorker.register('/static/js/service-worker.js', {scope: '/'});

        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicVapidKey)
        });

        const device_id = Date.now().toString();

        await sendRequest('/save_subscription/', {
            subscription: subscription,
            device_id: device_id
        });

        localStorage.setItem('device_id', device_id);
    } catch (error) {
        console.error('Error in run function:', error);
    }
}