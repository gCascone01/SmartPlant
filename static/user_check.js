
let userId = localStorage.getItem('user_id');

if (!userId) {
    userId = 'user-' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem('user_id', userId);
}

fetch('/check_user', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({ user_id: userId })
}).then(response => response.json())
.then(data => {
    if (data.status === 'wait'){
        window.location.href = "/wait";
    }
})
