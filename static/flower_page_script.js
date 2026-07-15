const instruction_modal = document.getElementById("instruction_modal");
const intructionButton = document.getElementById("intruction_button");
const closeButton = document.getElementById("close");
const name_modal = document.getElementById("name_modal");
const closeUsername = document.getElementById('close_username');
const usernameButton = document.getElementById("username_button");
const socket = io();

let timer
let logout = false;
let isRevealing = false;

socket.on('response', function(data) {
    console.log("Received AI response via socket:", data);
    
    let plantText = "";
    
    // The server attempts to parse the LLM output into a dictionary.
    // We must handle it whether it arrives as a string or an object.
    if (typeof data === 'string') {
        plantText = data;
    } else if (typeof data === 'object' && data !== null) {
        // Look for common keys your LLM might output, prioritizing 'answer'
        plantText = data.answer || data.message || data.response || JSON.stringify(data);
    }

    if (plantText) {
        appendMessage('Plant', plantText, 'plant-message');
    }
});

socket.on('new_art_available', function(data) {
    // Legge la durata inviata dal server, fall-back a 300000 se assente
    let totalDuration = (data && data.duration) ? data.duration : 300000;
    console.log(`Artwork ready. Duration configured via metabolic telemetry: ${totalDuration}ms.`);
    
    let artContainer = document.getElementById('art-container');
    let chatInput = document.getElementById('message');
    
    // NON disabilitiamo più l'input. Cambiamo solo il placeholder come feedback visivo.
    isRevealing = true;
    chatInput.placeholder = "Chat with Deliciosa while the art grows...";

    artContainer.innerHTML = '';

    let newImg = new Image();
    newImg.crossOrigin = "Anonymous"; 
    newImg.src = '/get_art?t=' + new Date().getTime(); 
    
    newImg.onload = function() {
        let width = newImg.width;
        let height = newImg.height;

        let sourceCanvas = document.createElement('canvas');
        sourceCanvas.width = width;
        sourceCanvas.height = height;
        let sCtx = sourceCanvas.getContext('2d', { willReadFrequently: true });
        sCtx.drawImage(newImg, 0, 0);
        
        let imgData = sCtx.getImageData(0, 0, width, height).data;

        let canvas = document.createElement('canvas');
        canvas.classList.add('art-reveal', 'visible');
        canvas.style.position = 'absolute';
        canvas.style.zIndex = '5';
        canvas.width = width;
        canvas.height = height;
        let ctx = canvas.getContext('2d');
        
        artContainer.appendChild(canvas);

        function getPixelColor(x, y) {
            x = Math.min(Math.max(Math.floor(x), 0), width - 1);
            y = Math.min(Math.max(Math.floor(y), 0), height - 1);
            let idx = (y * width + x) * 4;
            return `rgb(${imgData[idx]}, ${imgData[idx+1]}, ${imgData[idx+2]})`;
        }

        let phases = [32, 16, 8, 4, 2];
        let phaseDuration = totalDuration / phases.length; 
        
        let currentPhaseIndex = 0;
        let blocks = [];
        let totalPhaseBlocks = 0;
        let blocksRendered = 0;
        let phaseStartTime = null;

        function initPhase(index) {
            let size = phases[index];
            blocks = [];
            blocksRendered = 0;
            phaseStartTime = null;

            let cols = Math.ceil(width / size);
            let rows = Math.ceil(height / size);
            
            let rootX = width / 2;
            let rootY = height;

            for(let c = 0; c < cols; c++) {
                for(let r = 0; r < rows; r++) {
                    let targetX = c * size;
                    let targetY = r * size;
                    
                    let sampleX = targetX + (size / 2);
                    let sampleY = targetY + (size / 2);
                    let color = getPixelColor(sampleX, sampleY);
                    
                    let distY = rootY - sampleY;
                    let distX = Math.abs(rootX - sampleX);
                    
                    let mainBranch = Math.abs(Math.sin(sampleX * 0.004 + sampleY * 0.008) * Math.cos(sampleX * 0.006 - sampleY * 0.005));
                    let subBranch = Math.abs(Math.sin(sampleX * 0.02) * Math.cos(sampleY * 0.015));
                    let capillary = Math.abs(Math.sin(sampleX * 0.08 + sampleY * 0.05));
                    
                    let veinIntensity = (Math.pow(mainBranch, 0.4) * 450) + (Math.pow(subBranch, 1.5) * 200) + (capillary * 40);
                    let noise = Math.random() * 350; 
                    
                    let score = (distY * 2.8) + (distX * 1.5) - veinIntensity + noise;
                    
                    blocks.push({ x: targetX, y: targetY, size: size, color: color, score: score });
                }
            }
            
            blocks.sort((a, b) => a.score - b.score);
            totalPhaseBlocks = blocks.length;
        }

        initPhase(currentPhaseIndex);

        function renderCapillary(timestamp) {
            if (!phaseStartTime) phaseStartTime = timestamp;
            let elapsed = timestamp - phaseStartTime;
            let progress = Math.min(elapsed / phaseDuration, 1);

            let targetRender = Math.floor(progress * totalPhaseBlocks);
            
            while (blocksRendered < targetRender && blocksRendered < totalPhaseBlocks) {
                let b = blocks[blocksRendered];
                ctx.fillStyle = b.color;
                ctx.fillRect(b.x, b.y, b.size, b.size);
                blocksRendered++;
            }

            if (progress < 1) {
                requestAnimationFrame(renderCapillary);
            } else {
                currentPhaseIndex++;
                if (currentPhaseIndex < phases.length) {
                    initPhase(currentPhaseIndex);
                    requestAnimationFrame(renderCapillary);
                } else {
                    let finalImg = document.createElement('img');
                    ctx.drawImage(newImg, 0, 0); 
                    finalImg.src = canvas.toDataURL();
                    finalImg.classList.add('art-reveal', 'visible');
                    
                    artContainer.innerHTML = ''; 
                    artContainer.appendChild(finalImg);

                    // Ripristino dello stato della UI al completamento del tessuto grafico
                    isRevealing = false;
                    chatInput.placeholder = "Type your message to the plant...";
                    console.log("Organic rendering complete.");
                }
            }
        }
        requestAnimationFrame(renderCapillary);
    };
});

intructionButton.addEventListener("click", () => {
    instruction_modal.style.display = "block";
});

closeButton.addEventListener("click", () => {
    instruction_modal.style.display = "none";
});

usernameButton.addEventListener("click", () => {
    closeUsername.style.display = "block"
    name_modal.style.display = "block";
});

closeUsername.addEventListener("click", () => {
    name_modal.style.display = "none";
});

window.addEventListener("click", (event) => {
    if (event.target === instruction_modal) {
        instruction_modal.style.display = "none";
    }
});

document.addEventListener('DOMContentLoaded', () => {

    const textarea = document.getElementById('message');

    document.querySelectorAll("button, input, textarea").forEach(el => el.disabled = true);

    const hour = new Date().getHours();
    let message = "";

    if (hour > 7 && hour < 12) {
        message = "Good morning 🌞! Start a conversation with the plant.";
    } else if (hour >= 12 && hour < 19) {
        message = "Good afternoon 🌿! Start a conversation with the plant.";
    } else if (hour >= 19 || hour >= 0) {
        message = "Hello 🌙! Start a conversation with the plant.";
    }

    document.getElementById("welcome").textContent = message;

    timer = setTimeout(() => { inactivityPage() }, 10 * 60 * 1000);

    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
    });

    fetch('/check_new_user', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
    }).then(response => response.json())
        .then(data => {

            document.querySelectorAll("button, input, textarea").forEach(el => el.disabled = false);
            if (data.user) {
                instruction_modal.style.display = "block";
                localStorage.setItem('mood', data.mood);
                closeUsername.style.display = "none"
                name_modal.style.display = "block"
                localStorage.setItem('messages', data.messages)
                localStorage.setItem('session', data.session)


            } else {
                let username = localStorage.getItem('username');
                localStorage.setItem('messages', data.messages);
                let messages = Number(localStorage.getItem('messages'));
                localStorage.setItem('session', data.session);
                let session = Number(localStorage.getItem('session'));
                console.log("Messages: ", messages);

                if (!username) {
                    closeUsername.style.display = "none"
                    name_modal.style.display = "block"
                } else {
                    fetch('/send_name', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ "username": username })
                    })
                        .then(data => {
                            if (data.status === "error") {
                                alert("Please provide a valid name without numbers or spaces.")
                                closeUsername.style.display = "none"
                                name_modal.style.display = "block"
                            }

                        }).catch(error => {
                            console.error("Error sending name: ", error);
                        });
                }
            }
        })
});


document.getElementById("send_name").addEventListener("click", function () {

    username = document.getElementById("username").value.trim();

    if (username != "") {


        fetch('/send_name', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ "username": username })
        })
            .then(response => response.json())
            .then(data => {

                if (data.status === "error") {
                    alert("Please provide a valid name.")
                } else {
                    localStorage.setItem('username', username);
                    name_modal.style.display = "none";
                }
            }).catch(error => {
                console.error("Error sending name: ", error);

            });
    }
});


document.getElementById("send-to-llm").addEventListener("click", async function () {
    const button = document.getElementById("send-to-llm");
    const message_box = document.getElementById('message');

    if (message_box.value.trim() !== "") {
        button.disabled = true;
        message_box.disabled = true;
        const message = message_box.value;

        // Append the user's message to the UI stream immediately
        appendMessage('Human', message, 'human-message');

        if (timer) clearTimeout(timer);

        try {
            const response = await fetch('/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    is_revealing: isRevealing
                 })
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                message_box.value = "";
                let messages = parseInt(localStorage.getItem('messages') || 0);
                messages++;
                localStorage.setItem('messages', messages);
                
                // Note: We do NOT append the Plant's message here anymore.
                // The socket.on('response') listener will handle it when it arrives.

            } else if (response.ok && data.status === 'refresh') {
                alert("You have been disconnected. Please log in again.");
                window.location.reload();
            } else if (response.ok && data.status === 'no_connection') {
                alert("No connection to Raspberry Pi");
            } else {
                console.error("Server error");
            }

        } catch (error) {
            console.error("Error: ", error);
        } finally {
            button.disabled = false;
            message_box.disabled = false;
            message_box.focus();

            if (timer) clearTimeout(timer);
            timer = setTimeout(() => { inactivityPage() }, 10 * 60 * 1000);
        }
    }
});

document.getElementById("message").addEventListener("keypress", function (event) {
    if (event.key === "Enter") {
        document.getElementById("send-to-llm").click();
    }
});

document.getElementById("message").addEventListener("input", function () {
    if (timer) clearTimeout(timer);
    console.log("Resetting timer");
    timer = setTimeout(() => { inactivityPage() }, 10 * 60 * 1000);
});

window.addEventListener('pageshow', function (event) {
    if (event.persisted) {

        window.location.reload();
    }
});

document.getElementById("spray_button").addEventListener("click", () => {

    const confirmed = confirm("Have you sprayed the plant?");

    if (confirmed) {
        fetch("/spray_button", {
            method: "POST"
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === "success") {
                    console.log("Spray command sent successfully");
                } else if (data.status === "no_connection") {
                    console.error("No connection to Raspberry Pi");
                }
            })
            .catch(error => {
                console.error("Error: ", error);
            });
    }

});

document.getElementById("logout").addEventListener("click", async function () {
    try {
        logout = true
        const response = await fetch('/logout');
        const result = await response.json();

        if (result.status === 'success') {
            window.location.href = '/exit';
        } else {
            console.error("Server error");
        }
    } catch (error) {
        console.log(error);
    }
});

async function inactivityPage() {
    console.log("Logging out due to inactivity");
    try {
        const response = await fetch('/logout');
        const result = await response.json();

        if (result.status === 'success') {
            window.location.href = '/inactivity';
        } else {
            alert("No connection to the server.")
            console.error("Server error");
        }
    } catch (error) {
        console.log(error);
    }
}

async function inactivityPageServerLogout() {
    console.log("Logging out due to inactivity");
    try {

        window.location.href = '/inactivity';

    } catch (error) {
        console.log(error);
    }
}

document.addEventListener("visibilitychange", async function () {
    let userId = localStorage.getItem('user_id');

    if (document.visibilityState === "hidden" && !logout) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { inactivityPageServerLogout() }, 5 * 60 * 1000);
        fetch('/to_logout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                user_id: userId,
            })
        })
            .catch(err => console.error("Error:", err));
    } else if (document.visibilityState === "visible") {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { inactivityPage() }, 10 * 60 * 1000);

        fetch('/reset_last_activity', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                user_id: userId,
            })
        })
            .catch(err => console.error("Error:", err));

    }

});

function appendMessage(sender, text, className) {
    const historyContainer = document.getElementById('chat-history');
    
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message-bubble', className);
    
    const senderSpan = document.createElement('strong');
    senderSpan.textContent = sender;
    
    const textSpan = document.createElement('span');
    textSpan.textContent = text;
    
    msgDiv.appendChild(senderSpan);
    msgDiv.appendChild(textSpan);
    
    historyContainer.appendChild(msgDiv);
    
    // Auto-scroll to the newest message
    historyContainer.scrollTop = historyContainer.scrollHeight;
}

document.getElementById("force_art_button").addEventListener("click", () => {
    
    let chatInput = document.getElementById('message');
    let originalPlaceholder = chatInput.placeholder;
    chatInput.placeholder = "Simulating environment and generating art..."; 

    fetch("/force_art", {
        method: "POST"
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === "success") {
            console.log("Forced art command sent successfully.");
        } else if (data.status === "no_connection") {
            alert("No connection to Raspberry Pi");
            chatInput.placeholder = originalPlaceholder;
        }
    })
    .catch(error => {
        console.error("Error: ", error);
        chatInput.placeholder = originalPlaceholder;
    });
});
