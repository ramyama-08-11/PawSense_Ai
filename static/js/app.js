// Notify users if the page was opened from the filesystem (file://), which breaks API calls.
if (window.location.protocol === 'file:') {
    const msg = 'You opened the app from the filesystem. Start the Flask server and open http://127.0.0.1:5000/ instead.';
    try { alert(msg); } catch (e) { console.warn(msg); }
}

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const chatMessages = document.getElementById('chat-messages');
    const chatForm = document.getElementById('chat-form');
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const fileUpload = document.getElementById('file-upload');
    const filePreviewContainer = document.getElementById('file-preview-container');
    const filePreview = document.getElementById('file-preview');
    const removeFileBtn = document.getElementById('remove-file-btn');
    const historyList = document.getElementById('history-list');
    const newChatBtn = document.getElementById('new-chat-btn');
    const currentChatTitle = document.getElementById('current-chat-title');
    const themeBtn = document.getElementById('theme-btn');
    
    // Pre-fetch voices for TTS
    if ('speechSynthesis' in window) {
        window.speechSynthesis.getVoices();
        window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
    }
    
    let currentSessionId = null;
    let isWaitingForResponse = false;
    let selectedFiles = [];
    let selectedFilePreviews = []; // data URLs for previews, parallel to selectedFiles
    let selectedFileCaptions = []; // per-image captions
    const inputWrapper = document.getElementById('input-wrapper');

    // Small UI hint for paste/drag support
    if (inputWrapper) {
        const hint = document.createElement('div');
        hint.id = 'paste-drag-hint';
        hint.style.fontSize = '0.80rem';
        hint.style.color = 'var(--text-secondary)';
        hint.style.marginTop = '6px';
        hint.textContent = 'Tip: Paste images (Ctrl+V) or drag files into the chat to upload.';
        inputWrapper.appendChild(hint);
    }

    // =====================================================================
    // ANIMATED CANVAS BACKGROUND
    // =====================================================================
    (function initCanvas() {
        const canvas = document.getElementById('bg-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        let orbs = [];

        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        resize();
        window.addEventListener('resize', resize);

        function isDark() {
            return document.documentElement.getAttribute('data-theme') === 'dark';
        }

        for (let i = 0; i < 4; i++) {
            orbs.push({
                x: Math.random() * window.innerWidth,
                y: Math.random() * window.innerHeight,
                r: 180 + Math.random() * 200,
                dx: (Math.random() - 0.5) * 0.4,
                dy: (Math.random() - 0.5) * 0.4,
                hue: [220, 260, 200, 280][i],
            });
        }

        function draw() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const alpha = isDark() ? 0.18 : 0.10;
            orbs.forEach(o => {
                o.x += o.dx; o.y += o.dy;
                if (o.x < -o.r) o.x = canvas.width + o.r;
                if (o.x > canvas.width + o.r) o.x = -o.r;
                if (o.y < -o.r) o.y = canvas.height + o.r;
                if (o.y > canvas.height + o.r) o.y = -o.r;
                const g = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, o.r);
                g.addColorStop(0, `hsla(${o.hue},80%,65%,${alpha})`);
                g.addColorStop(1, `hsla(${o.hue},80%,65%,0)`);
                ctx.beginPath();
                ctx.arc(o.x, o.y, o.r, 0, Math.PI * 2);
                ctx.fillStyle = g;
                ctx.fill();
            });
            requestAnimationFrame(draw);
        }
        draw();
    })();


    // Theme Setup
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);

    themeBtn.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        updateThemeIcon(newTheme);
    });

    function updateThemeIcon(theme) {
        themeBtn.innerHTML = theme === 'light' ? '<i class="fas fa-moon"></i>' : '<i class="fas fa-sun"></i>';
    }

    // Auto-resize textarea and typing glow
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
        const wrapper = this.closest('.input-wrapper');
        
        if (this.value.trim() || (selectedFiles && selectedFiles.length)) {
            sendBtn.disabled = false;
            wrapper.classList.add('is-typing');
        } else {
            sendBtn.disabled = true;
            wrapper.classList.remove('is-typing');
        }
    });
    
    // Remove typing glow on blur if empty
    messageInput.addEventListener('blur', function() {
        if (!this.value.trim() && !(selectedFiles && selectedFiles.length)) {
            this.closest('.input-wrapper').classList.remove('is-typing');
        }
    });

    // Enter to send; Shift+Enter keeps a newline for longer prompts.
    messageInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            const hasText = this.value.trim().length > 0;
            const hasFiles = Boolean(selectedFiles && selectedFiles.length);
            if ((hasText || hasFiles) && !isWaitingForResponse) {
                e.preventDefault();
                if (chatForm) {
                    chatForm.requestSubmit();
                }
            }
        }
    });

    // File Upload Handling
    fileUpload.addEventListener('change', function(e) {
        if (this.files && this.files.length) {
            // replace current selection
            selectedFiles = Array.from(this.files);
            selectedFilePreviews = [];
            selectedFileCaptions = [];
            filePreviewContainer.innerHTML = '';
            selectedFiles.forEach((f, idx) => {
                const reader = new FileReader();
                reader.onload = function(ev) {
                    selectedFilePreviews.push(ev.target.result);
                    selectedFileCaptions.push('');
                    addFilePreviewElement(f, ev.target.result, idx);
                }
                reader.readAsDataURL(f);
            });
            filePreviewContainer.style.display = 'block';
            sendBtn.disabled = false;
        }
    });

    // Handle pasted images into the chat input (Ctrl+V / Cmd+V)
    document.addEventListener('paste', function(e) {
        try {
            const items = (e.clipboardData || e.originalEvent.clipboardData).items;
            if (!items) return;
            for (let i = 0; i < items.length; i++) {
                const item = items[i];
                if (item.kind === 'file' && item.type.startsWith('image/')) {
                    const blob = item.getAsFile ? item.getAsFile() : item.getAsBlob();
                    if (!blob) continue;
                    // Create a File object if necessary
                    const file = new File([blob], 'pasted-image.' + (blob.type.split('/')[1] || 'png'), { type: blob.type });
                    // append to selection
                    selectedFiles.push(file);
                    const reader = new FileReader();
                    reader.onload = function(ev) {
                        selectedFilePreviews.push(ev.target.result);
                        selectedFileCaptions.push('');
                        addFilePreviewElement(file, ev.target.result, selectedFilePreviews.length-1);
                        filePreviewContainer.style.display = 'block';
                        sendBtn.disabled = false;
                        messageInput.dispatchEvent(new Event('input'));
                    };
                    reader.readAsDataURL(file);
                    // prevent the default paste (so the image doesn't get inserted into the contenteditable by browsers)
                    e.preventDefault();
                    return;
                }
            }
        } catch (err) {
            // ignore
        }
    });

    removeFileBtn.addEventListener('click', () => {
        clearFilePreview();
    });

    function clearFilePreview() {
        fileUpload.value = '';
        selectedFiles = [];
        selectedFilePreviews = [];
        selectedFileCaptions = [];
        filePreviewContainer.style.display = 'none';
        filePreviewContainer.innerHTML = '';
        if (!messageInput.value.trim()) {
            sendBtn.disabled = true;
        }
    }

    // Show file preview and optional caption input
    function addFilePreviewElement(file, dataUrl, idx) {
        // thumbnail wrapper
        const thumbWrap = document.createElement('div');
        thumbWrap.className = 'multi-thumb';
        thumbWrap.style.display = 'inline-block';
        thumbWrap.style.position = 'relative';
        thumbWrap.style.marginRight = '8px';

        const img = document.createElement('img');
        img.src = dataUrl;
        img.style.maxWidth = '140px';
        img.style.maxHeight = '120px';
        img.style.borderRadius = '8px';
        img.style.display = 'block';
        thumbWrap.appendChild(img);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn-icon';
        removeBtn.style.position = 'absolute';
        removeBtn.style.top = '6px';
        removeBtn.style.right = '6px';
        removeBtn.innerHTML = '<i class="fas fa-times"></i>';
        removeBtn.addEventListener('click', () => {
            // remove from arrays and DOM
            selectedFiles.splice(idx, 1);
            selectedFilePreviews.splice(idx, 1);
            selectedFileCaptions.splice(idx, 1);
            thumbWrap.remove();
            if (selectedFiles.length === 0) {
                filePreviewContainer.style.display = 'none';
                sendBtn.disabled = !messageInput.value.trim();
            }
        });
        thumbWrap.appendChild(removeBtn);

        // per-image caption input
        const capInput = document.createElement('input');
        capInput.type = 'text';
        capInput.placeholder = 'Caption (optional)';
        capInput.style.width = '140px';
        capInput.style.marginTop = '6px';
        capInput.style.padding = '6px';
        capInput.style.borderRadius = '6px';
        capInput.addEventListener('input', (e)=>{
            selectedFileCaptions[idx] = e.target.value;
        });
        thumbWrap.appendChild(capInput);

        filePreviewContainer.appendChild(thumbWrap);
        sendBtn.disabled = false;

        // add caption input if not present
        let caption = document.getElementById('image-caption-input');
        if (!caption) {
            caption = document.createElement('input');
            caption.id = 'image-caption-input';
            caption.type = 'text';
            caption.placeholder = 'Add a short note about this image (optional)';
            caption.style.width = '100%';
            caption.style.marginTop = '8px';
            caption.style.padding = '8px 10px';
            caption.style.borderRadius = '8px';
            caption.style.border = '1px solid var(--border-color)';
            caption.addEventListener('input', () => {
                // if message input is empty, mirror caption there for convenience
                if (!messageInput.value.trim()) messageInput.value = caption.value;
            });
            filePreviewContainer.appendChild(caption);
        }
        // keep caption in sync
        const captionEl = document.getElementById('image-caption-input');
        if (captionEl) captionEl.value = '';
        // add optional image-question input and action button
        let qWrapper = document.getElementById('image-question-wrapper');
        if (!qWrapper) {
            qWrapper = document.createElement('div');
            qWrapper.id = 'image-question-wrapper';
            qWrapper.style.display = 'flex';
            qWrapper.style.gap = '8px';
            qWrapper.style.marginTop = '8px';

            const qInput = document.createElement('input');
            qInput.id = 'image-question-input';
            qInput.type = 'text';
            qInput.placeholder = 'Ask a question about this image (e.g. "Is this rash serious?")';
            qInput.style.flex = '1';
            qInput.style.padding = '8px 10px';
            qInput.style.borderRadius = '8px';
            qInput.style.border = '1px solid var(--border-color)';

            const qBtn = document.createElement('button');
            qBtn.id = 'image-question-btn';
            qBtn.className = 'btn-primary small';
            qBtn.textContent = 'Ask about image';
            qBtn.addEventListener('click', (ev) => {
                ev.preventDefault();
                const q = document.getElementById('image-question-input').value.trim();
                if (!q) return;
                messageInput.value = q;
                messageInput.dispatchEvent(new Event('input'));
                chatForm.dispatchEvent(new Event('submit'));
            });

            qWrapper.appendChild(qInput);
            qWrapper.appendChild(qBtn);
            filePreviewContainer.appendChild(qWrapper);
        } else {
            const qInput = document.getElementById('image-question-input');
            if (qInput) qInput.value = '';
        }
    }

    // Load History
    async function loadSessions() {
        try {
            const response = await fetch('/api/sessions');
            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }
            const sessions = await response.json();
            
            historyList.innerHTML = '';
            function createHistoryItem(session) {
                const li = document.createElement('li');
                li.className = 'history-item';
                if (session.id === currentSessionId) li.classList.add('active');
                
                li.innerHTML = `
                    <i class="far fa-comment"></i>
                    <span class="session-title">${session.title}</span>
                    <button class="delete-session-btn" title="Delete conversation">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                `;
                
                li.addEventListener('click', () => {
                    currentSessionId = session.id;
                    document.getElementById('current-chat-title').textContent = session.title;
                    loadSession(session.id, session.title);
                });

                // Delete specific session
                const deleteBtn = li.querySelector('.delete-session-btn');
                deleteBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    if (confirm('Delete this conversation?')) {
                        try {
                            const response = await fetch(`/api/sessions/${session.id}`, {
                                method: 'DELETE'
                            });
                            if (response.ok) {
                                li.remove();
                                if (session.id === currentSessionId) {
                                    currentSessionId = null;
                                    chatMessages.innerHTML = '';
                                    currentChatTitle.textContent = 'New Conversation';
                                }
                            }
                        } catch (err) {
                            console.error('Failed to delete session:', err);
                        }
                    }
                });
                
                return li;
            }

            sessions.forEach(session => {
                historyList.appendChild(createHistoryItem(session));
            });
            
            if (!currentSessionId && sessions.length > 0) {
                // We don't auto-load the first session to keep the "New Chat" feel
            }
        } catch (error) {
            console.error('Error loading sessions:', error);
        }
    }

    // Create New Session
    async function createNewSession() {
        try {
            const response = await fetch('/api/sessions', { method: 'POST' });
            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }
            const data = await response.json();
            currentSessionId = data.id;
            currentChatTitle.textContent = data.title;
            
            // Clear chat messages and show welcome screen
            chatMessages.innerHTML = `
                <div class="welcome-screen">
                    <div class="welcome-hero">
                        <div class="welcome-icon-ring"><div class="welcome-icon"><i class="fas fa-paw"></i></div></div>
                        <h1>Hi, I'm <span class="gradient-text">Pawsense AI</span></h1>
                        <p>Your intelligent pet care companion. Ask me anything about your pets!</p>
                    </div>
                    <div class="feature-cards">
                        <div class="feature-card" onclick="setPrompt('My pet seems unwell. What symptoms should I watch for?')">
                            <div class="fc-icon" style="background:linear-gradient(135deg,#f59e0b,#ef4444)"><i class="fas fa-heartbeat"></i></div>
                            <h4>Health Check</h4><p>Spot signs of illness early</p>
                        </div>
                        <div class="feature-card" onclick="setPrompt('What is the best diet plan for my dog?')">
                            <div class="fc-icon" style="background:linear-gradient(135deg,#10b981,#3b82f6)"><i class="fas fa-utensils"></i></div>
                            <h4>Nutrition Guide</h4><p>Optimise your pet's diet</p>
                        </div>
                        <div class="feature-card" onclick="setPrompt('How do I train my puppy to sit and stay?')">
                            <div class="fc-icon" style="background:linear-gradient(135deg,#8b5cf6,#ec4899)"><i class="fas fa-award"></i></div>
                            <h4>Training Tips</h4><p>Positive-reinforcement methods</p>
                        </div>
                        <div class="feature-card" onclick="setPrompt('What vaccinations does my pet need this year?')">
                            <div class="fc-icon" style="background:linear-gradient(135deg,#3b82f6,#06b6d4)"><i class="fas fa-syringe"></i></div>
                            <h4>Vaccine Schedule</h4><p>Keep immunisations up to date</p>
                        </div>
                    </div>
                    <p class="welcome-tip"><i class="fas fa-camera"></i> Tip: Upload a photo for instant AI visual analysis!</p>
                </div>
            `;
            
            clearFilePreview();
            messageInput.value = '';
            messageInput.style.height = 'auto';
            sendBtn.disabled = true;
            
            await loadSessions();
        } catch (error) {
            console.error('Error creating session:', error);
        }
    }

    // Load Specific Session
    async function loadSession(sessionId, title) {
        currentSessionId = sessionId;
        currentChatTitle.textContent = title;
        
        // Update active class in sidebar
        document.querySelectorAll('.history-item').forEach(item => {
            item.classList.remove('active');
        });
        await loadSessions(); 
        
        try {
            const response = await fetch(`/api/sessions/${sessionId}/messages`);
            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }
            const messages = await response.json();
            
            chatMessages.innerHTML = '';
            if (messages.length === 0) {
                chatMessages.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon"><i class="fas fa-cat"></i></div>
                        <h3>Welcome to Pawsense</h3>
                        <p>Ask me anything about your pets, upload a photo for analysis, or just say hello!</p>
                    </div>
                `;
            } else {
                messages.forEach(msg => {
                    appendMessage(msg.role, msg.content, msg.image_path, msg.image_caption);
                });
                scrollToBottom();
            }
        } catch (error) {
            console.error('Error loading messages:', error);
        }
    }

    newChatBtn.addEventListener('click', createNewSession);

    // Send Message
    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const content = messageInput.value.trim();
        if (!content && !(selectedFiles && selectedFiles.length)) return;

        // Route natural language queries about finding nearest vets directly to the locator
        const isVetLocatorIntent = /(find|show|search|nearest|nearby|local|emergency)\s+(vet|veterinar|hospital|clinic)/i.test(content) ||
                                  /(vet|veterinar|hospital|clinic)\s+(near|close|around|location)/i.test(content);
        if (isVetLocatorIntent && !(selectedFiles && selectedFiles.length)) {
            messageInput.value = '';
            messageInput.style.height = 'auto';
            findNearbyVets();
            return;
        }

        if (!currentSessionId) {
            await createNewSession();
        }

        // Add user message to UI immediately
        const userContent = content;
        const userImageSrc = (selectedFilePreviews && selectedFilePreviews.length) ? selectedFilePreviews.slice() : null;
        const captionEl = document.getElementById('image-caption-input');
        const userCaption = (captionEl && captionEl.value) ? captionEl.value.trim() : null;
        appendMessage('user', userContent, userImageSrc, userCaption);
        
        // Prepare FormData (for normal chat)
        const formData = new FormData();
        formData.append('session_id', currentSessionId);
        formData.append('content', content);
        
        const langSelect = document.getElementById('language-select');
        if (langSelect) {
            formData.append('language', langSelect.value);
        }
        
        if (selectedFiles && selectedFiles.length) {
            selectedFiles.forEach((f) => formData.append('image', f));
            // append per-image captions if present
            if (selectedFileCaptions && selectedFileCaptions.length) {
                selectedFileCaptions.forEach((c) => formData.append('image_caption', c || ''));
            } else {
                // fallback to global caption input
                const captionEl = document.getElementById('image-caption-input');
                if (captionEl && captionEl.value.trim()) formData.append('image_caption', captionEl.value.trim());
            }
        }

        // Reset input
        messageInput.value = '';
        messageInput.style.height = 'auto';
        clearFilePreview();
        isWaitingForResponse = true;
        sendBtn.disabled = true;
        
        // Remove empty state if present
        const emptyState = document.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        // Show typing indicator
        showTypingIndicator();
        scrollToBottom();

        try {
            const customInstructions = localStorage.getItem('custom_instructions') || '';
                let response, data;
                // Always send to the normal chat endpoint (no structured mode)
                response = await fetch('/api/chat', { method: 'POST', body: formData, credentials: 'same-origin' });

            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }

            if (!response.ok) {
                let text = '';
                try { text = await response.text(); } catch (e) { /* ignore */ }
                removeTypingIndicator();
                appendMessage('model', `Server Error ${response.status}: ${text || response.statusText}`);
                console.error('Server error response', response.status, text);
                return;
            }

            data = await response.json();

            removeTypingIndicator();

            if (data.error) {
                appendMessage('model', `Error: ${data.error}`);
            } else {
                if (data.model_message) {
                    const displaySrc = data.model_message.thumb_path || data.model_message.image_path || data.model_message.image_data || null;
                    const fullSrc = data.model_message.image_path || data.model_message.image_data || null;
                    const modelCaption = data.model_message.image_caption || null;
                    appendMessage('model', data.model_message.content, { display: displaySrc, full: fullSrc }, modelCaption, true);
                } else if (data.model_text) {
                    appendMessage('model', data.model_text);
                } else if (data.structured && data.model_text) {
                    // If backend returned structured payload, show human text
                    appendMessage('model', data.model_text);
                } else {
                    appendMessage('model', JSON.stringify(data));
                }
                loadSessions();
            }
        } catch (error) {
            removeTypingIndicator();
            const em = (error && error.message) ? error.message : String(error);
            appendMessage('model', `Sorry, I couldn't connect to the server. (${em})`);
            console.error('Error sending message:', error);
        } finally {
            isWaitingForResponse = false;
            scrollToBottom();
        }
    });

    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function appendMessage(role, content, imagePathOrDataUrl = null, caption = null, shouldStream = false) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        
        const icon = role === 'user' ? 'fa-user' : 'fa-paw';
        
            let imageHtml = '';
            let fullSrcAttr = '';
            if (imagePathOrDataUrl) {
                // Support arrays of images or single image
                if (Array.isArray(imagePathOrDataUrl)) {
                    imagePathOrDataUrl.forEach((item, idx) => {
                        let display = null, full = null;
                        if (typeof item === 'string') display = item;
                        else if (typeof item === 'object') { display = item.display; full = item.full; }
                        const src = display && display.startsWith('data:') ? display : (display ? `/${display}` : '');
                        if (src) {
                            const fattr = full ? ` data-full="/${full}"` : '';
                            imageHtml += `<div class="img-wrap"><img src="${src}" alt="Attached image"${fattr}></div>`;
                            // caption may be an array matching images or a single string
                            if (Array.isArray(caption) && caption[idx]) imageHtml += `<div class="img-caption">${escapeHtml(caption[idx])}</div>`;
                            else if (caption && !Array.isArray(caption)) imageHtml += `<div class="img-caption">${escapeHtml(caption)}</div>`;
                        }
                    });
                } else {
                    // imagePathOrDataUrl may be a string or an object {display, full}
                    let display = null, full = null;
                    if (typeof imagePathOrDataUrl === 'string') display = imagePathOrDataUrl;
                    else if (typeof imagePathOrDataUrl === 'object') { display = imagePathOrDataUrl.display; full = imagePathOrDataUrl.full; }
                    const src = display && display.startsWith('data:') ? display : (display ? `/${display}` : '');
                    if (src) {
                        if (full) fullSrcAttr = ` data-full="/${full}"`;
                        imageHtml = `
                            <div class="img-wrap">
                                <img src="${src}" alt="Attached image"${fullSrcAttr}>
                            </div>`;
                        if (caption) {
                            imageHtml += `<div class="img-caption">${escapeHtml(caption)}</div>`;
                        }
                    } else {
                        imageHtml = '';
                    }
                }
            }
        
        // Parse markdown if it's from model
        const formattedContent = role === 'model' ? marked.parse(content || '') : (content || '').replace(/\n/g, '<br>');

        msgDiv.innerHTML = `
            <div class="avatar"><i class="fas ${icon}"></i></div>
            <div class="message-content">
                ${imageHtml}
                <div class="msg-text-container"></div>
                ${role === 'model' ? `<button class="tts-btn" title="Read Aloud"><i class="fas fa-volume-up"></i></button>` : ''}
            </div>
        `;
        
        chatMessages.appendChild(msgDiv);
        const textContainer = msgDiv.querySelector('.msg-text-container');

        if (shouldStream && role === 'model') {
            // Simple streaming logic: parse HTML, then type it out word by word
            let i = 0;
            textContainer.innerHTML = '';
            
            // To prevent breaking HTML tags during streaming, we type text nodes
            // But for simplicity, we can reveal words in a temporary container.
            // A safer robust way:
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = formattedContent;
            
            // We just stream characters of the raw text and then flip to markdown? 
            // Better: just render the full HTML, set opacity to 0, and then fade words in.
            textContainer.innerHTML = formattedContent;
            const elements = textContainer.querySelectorAll('*');
            elements.forEach(el => {
                if(el.children.length === 0 && el.textContent.trim() !== '') {
                    const words = el.textContent.split(' ');
                    el.innerHTML = words.map(w => `<span style="opacity:0; transition: opacity 0.1s; display:inline-block;">${w}&nbsp;</span>`).join('');
                }
            });
            
            const spans = textContainer.querySelectorAll('span');
            let delay = 0;
            spans.forEach((span, idx) => {
                setTimeout(() => {
                    span.style.opacity = '1';
                    scrollToBottom();
                }, delay);
                delay += 25; // 25ms per word
            });
        } else {
            textContainer.innerHTML = formattedContent;
        }

        // Add Text-to-Speech logic for model messages
        if (role === 'model') {
            
            const ttsBtn = msgDiv.querySelector('.tts-btn');
            if (ttsBtn) {
                ttsBtn.addEventListener('click', () => {
                    if (ttsBtn.classList.contains('speaking')) {
                        window.speechSynthesis.cancel();
                        ttsBtn.classList.remove('speaking');
                        ttsBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
                    } else {
                        window.speechSynthesis.cancel(); // Stop any current speech
                        
                        const langSelect = document.getElementById('language-select');
                        const selectedLang = langSelect ? langSelect.value : 'English';
                        const langCodes = { 
                            'English': 'en-US', 'Spanish': 'es-ES', 'French': 'fr-FR', 
                            'Hindi': 'hi-IN', 'Kannada': 'kn-IN', 'Telugu': 'te-IN',
                            'Tamil': 'ta-IN', 'Malayalam': 'ml-IN', 'Bengali': 'bn-IN',
                            'Mandarin': 'zh-CN', 'Japanese': 'ja-JP', 'German': 'de-DE' 
                        };
                        const targetLang = langCodes[selectedLang] || 'en-US';

                        const visibleText = (textContainer && textContainer.textContent) ? textContainer.textContent : content;
                        const spokenText = visibleText
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim();

                        const utterance = new SpeechSynthesisUtterance(spokenText || content);
                        utterance.lang = targetLang;
                        
                        // Apply speech speed from settings
                        const speedSlider = document.getElementById('speech-speed-range');
                        if (speedSlider) {
                            utterance.rate = parseFloat(speedSlider.value);
                        }
                        
                        // Find the best voice for the selected language
                        const voices = window.speechSynthesis.getVoices();
                        let voice = voices.find(v => v.lang === targetLang || v.lang.replace('_', '-').startsWith(targetLang.split('-')[0]));
                        
                        // More aggressive search by language name or code if matching fails
                        if (!voice) {
                            const searchTerms = {
                                'kn-IN': ['kannada', 'kn', 'ಕನ್ನಡ'],
                                'hi-IN': ['hindi', 'hi', 'हिन्दी'],
                                'te-IN': ['telugu', 'te', 'తెలుగు'],
                                'ta-IN': ['tamil', 'ta', 'தமிழ்'],
                                'ml-IN': ['malayalam', 'ml', 'മലയാളം'],
                                'bn-IN': ['bengali', 'bn', 'বাংলা']
                            };
                            const terms = searchTerms[targetLang];
                            if (terms) {
                                voice = voices.find(v => {
                                    const name = v.name.toLowerCase();
                                    const vlang = v.lang.toLowerCase();
                                    return terms.some(t => name.includes(t) || vlang.includes(t));
                                });
                            }
                        }
                        
                        if (voice) {
                            utterance.voice = voice;
                            console.log('Selected voice:', voice.name);
                        } else if (targetLang !== 'en-US') {
                            console.warn(`No native voice found for ${targetLang}. Browser will attempt default matching.`);
                        }

                        const stopTranslations = {
                            'kn-IN': 'ಓದುವುದನ್ನು ನಿಲ್ಲಿಸಿ', 'hi-IN': 'पढ़ना बंद करें',
                            'te-IN': 'చదవడం ఆపివేయి', 'ta-IN': 'வாசிப்பதை நிறுத்து',
                            'ml-IN': 'വാಯന ನಿർത്തുക', 'bn-IN': 'পড়া বন্ধ করুন',
                            'es-ES': 'Dejar de leer', 'fr-FR': 'Arrêter la lecture',
                            'zh-CN': '停止朗读', 'ja-JP': '読み上げを停止', 'de-DE': 'Lesen stoppen'
                        };
                        const stopText = stopTranslations[targetLang] || 'Stop reading';

                        window.speechSynthesis.speak(utterance);
                        ttsBtn.classList.add('speaking');
                        ttsBtn.innerHTML = `<i class="fas fa-volume-mute"></i> ${stopText}`;
                        
                        utterance.onend = () => {
                            ttsBtn.classList.remove('speaking');
                            ttsBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
                        };
                        
                        utterance.onerror = () => {
                            ttsBtn.classList.remove('speaking');
                            ttsBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
                        }
                    }
                });
            }
        }
    }

    // Insert raw HTML message (used for structured scan analysis cards)
    function appendRawMessage(role, htmlContent, imagePathOrDataUrl = null) {
        const msgDiv = document.createElement('div');
        const msgId = 'msg-' + Date.now().toString(36) + '-' + Math.floor(Math.random()*10000);
        msgDiv.id = msgId;
        msgDiv.className = `message ${role}`;

        const icon = role === 'user' ? 'fa-user' : 'fa-paw';

        let imageHtml = '';
        if (imagePathOrDataUrl) {
            const src = imagePathOrDataUrl.startsWith('data:') ? imagePathOrDataUrl : `/${imagePathOrDataUrl}`;
            imageHtml = `<img src="${src}" alt="Attached image">`;
        }

        msgDiv.innerHTML = `
            <div class="avatar"><i class="fas ${icon}"></i></div>
            <div class="message-content">
                ${imageHtml}
                <div class="msg-text-container">${htmlContent}</div>
            </div>
        `;
        chatMessages.appendChild(msgDiv);
        scrollToBottom();
        return msgId;
    }

    function showTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'message model';
        indicator.id = 'typing-indicator';
        indicator.innerHTML = `
            <div class="avatar"><i class="fas fa-paw"></i></div>
            <div class="ai-thinking-wrapper">
                <div class="ai-thinking-dots">
                    <span></span><span></span><span></span>
                </div>
                <span class="ai-thinking-label">Pawsense is thinking…</span>
            </div>
        `;
        chatMessages.appendChild(indicator);
    }

    function removeTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Mobile Menu
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const sidebar = document.querySelector('.sidebar');
    
    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', () => {
            sidebar.classList.toggle('open');
        });
    }

    // Clear History Feature
    const clearHistoryBtn = document.getElementById('clear-history-btn');
    if (clearHistoryBtn) {
        clearHistoryBtn.addEventListener('click', async () => {
            if(confirm("Are you sure you want to delete all chat history?")) {
                try {
                    const response = await fetch('/api/sessions', { method: 'DELETE' });
                    if (response.status === 401) {
                        window.location.href = '/login';
                        return;
                    }
                    if(response.ok) {
                        currentSessionId = null;
                        currentChatTitle.textContent = "New Conversation";
                        chatMessages.innerHTML = `
                            <div class="empty-state">
                                <div class="empty-icon"><i class="fas fa-cat"></i></div>
                                <h3>Welcome to Pawsense</h3>
                                <p>Ask me anything about your pets, upload a photo for analysis, or just say hello!</p>
                            </div>
                        `;
                        await loadSessions();
                    }
                } catch(e) {
                    console.error("Error clearing history", e);
                }
            }
        });
    }

    // Voice Input: record audio and send it to backend for Google Speech-to-Text conversion.
    const voiceBtn = document.getElementById('voice-btn');
    if (voiceBtn) {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            voiceBtn.style.display = 'none';
        } else {
            let mediaRecorder = null;
            let audioStream = null;
            let audioChunks = [];
            let isRecording = false;

            function setVoiceState(recording) {
                isRecording = recording;
                voiceBtn.classList.toggle('recording', recording);
                voiceBtn.title = recording ? 'Stop recording' : 'Record voice message';
                messageInput.placeholder = recording ? 'Recording… speak now' : 'Ask anything about your pet…';
            }

            async function submitRecordedAudio(blob) {
                const formData = new FormData();
                const audioFile = new File([blob], `voice-${Date.now()}.webm`, { type: blob.type || 'audio/webm' });
                formData.append('audio', audioFile);

                messageInput.placeholder = 'Transcribing…';
                const response = await fetch('/api/transcribe', {
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin'
                });

                if (!response.ok) {
                    let details = '';
                    try { details = await response.text(); } catch (err) { /* ignore */ }
                    throw new Error(details || 'Audio transcription failed');
                }

                const data = await response.json();
                if (!data.transcript) {
                    throw new Error('No transcript returned');
                }

                messageInput.value = data.transcript;
                messageInput.dispatchEvent(new Event('input', { bubbles: true }));
                messageInput.placeholder = 'Ask anything about your pet…';
                if (chatForm) {
                    chatForm.requestSubmit();
                }
            }

            voiceBtn.addEventListener('click', async () => {
                if (isRecording) {
                    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                        mediaRecorder.stop();
                    }
                    return;
                }

                try {
                    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                        ? 'audio/webm;codecs=opus'
                        : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/mp4');
                    mediaRecorder = new MediaRecorder(audioStream, mimeType ? { mimeType } : undefined);
                    audioChunks = [];

                    mediaRecorder.ondataavailable = (event) => {
                        if (event.data && event.data.size > 0) {
                            audioChunks.push(event.data);
                        }
                    };

                    mediaRecorder.onstop = async () => {
                        const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                        if (audioStream) {
                            audioStream.getTracks().forEach(track => track.stop());
                            audioStream = null;
                        }
                        setVoiceState(false);

                        try {
                            await submitRecordedAudio(blob);
                        } catch (error) {
                            const message = (error && error.message) ? error.message : String(error);
                            console.error('Voice transcription error:', error);
                            alert('Voice transcription failed. Please check microphone access and try again.');
                            messageInput.placeholder = 'Ask anything about your pet…';
                            if (messageInput.value.trim() === '') {
                                messageInput.value = '';
                            }
                        }
                    };

                    setVoiceState(true);
                    mediaRecorder.start();
                } catch (error) {
                    console.error('Mic access error:', error);
                    alert('Microphone permission is required to record audio. Please allow it and try again.');
                }
            });
        }
    }

    // Admin cleanup button (visible only to user id 1 in template)
    const adminCleanupBtn = document.getElementById('admin-cleanup-btn');
    if (adminCleanupBtn) {
        adminCleanupBtn.addEventListener('click', async () => {
            if (!confirm('Run cleanup of generated files now?')) return;
            adminCleanupBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
            try {
                const resp = await fetch('/admin/cleanup', { method: 'POST', credentials: 'same-origin' });
                const data = await resp.json();
                if (resp.ok) {
                    alert('Cleanup complete. Deleted: ' + (data.deleted.length ? data.deleted.join(', ') : 'none'));
                } else {
                    alert('Cleanup failed: ' + (data.error || resp.statusText));
                }
            } catch (err) {
                console.error('Cleanup error', err);
                alert('Cleanup request failed. Check console.');
            } finally {
                adminCleanupBtn.innerHTML = '<i class="fas fa-broom"></i> Run Cleanup (Admin)';
            }
        });
    }

    // Open generated images gallery
    const openGalleryBtn = document.getElementById('open-gallery-btn');
    const galleryModal = document.getElementById('gallery-modal');
    const closeGalleryBtn = document.getElementById('close-gallery-btn');
    const galleryGrid = document.getElementById('gallery-grid');
    const galleryEmpty = document.getElementById('gallery-empty');
    if (openGalleryBtn) {
        openGalleryBtn.addEventListener('click', async () => {
            galleryGrid.innerHTML = '';
            galleryEmpty.style.display = 'none';
            galleryModal.style.display = 'flex';
            try {
                const resp = await fetch('/api/my-images');
                if (!resp.ok) throw resp;
                const data = await resp.json();
                const imgs = data.images || [];
                if (!imgs.length) {
                    galleryEmpty.style.display = 'block';
                    return;
                }
                imgs.forEach(i => {
                    const cell = document.createElement('div'); cell.className='gallery-cell';
                    const thumbSrc = i.thumb_path ? `/${i.thumb_path}` : (i.image_path ? `/${i.image_path}` : '');
                    const fullSrc = i.image_path ? `/${i.image_path}` : thumbSrc;
                    cell.innerHTML = `
                        <div class="gallery-thumb-wrap">
                            <img src="${thumbSrc}" data-full="${fullSrc}" alt="${i.filename}">
                        </div>
                        <div style="display:flex;gap:8px;margin-top:6px;align-items:center;justify-content:space-between;">
                            <small style="color:var(--text-secondary)">${new Date(i.created_at).toLocaleString()}</small>
                            <div>
                                <button class="btn-outline small gallery-download">Download</button>
                                <button class="btn-primary small gallery-showcase" data-mid="${i.message_id}">Showcase</button>
                                <button class="btn-outline small gallery-delete" data-fname="${i.filename}" data-mid="${i.message_id}">Delete</button>
                            </div>
                        </div>`;
                    galleryGrid.appendChild(cell);
                });
                // attach handlers
                galleryGrid.querySelectorAll('.gallery-download').forEach(btn => {
                    btn.addEventListener('click', (e)=>{
                        const img = e.target.closest('.gallery-cell').querySelector('img');
                        const href = img.getAttribute('data-full');
                        const a = document.createElement('a'); a.href = href; a.download = href.split('/').pop(); a.click();
                    });
                });
                galleryGrid.querySelectorAll('.gallery-delete').forEach(btn => {
                    btn.addEventListener('click', async (e)=>{
                        if(!confirm('Delete this generated image?')) return;
                        const fname = btn.getAttribute('data-fname');
                        const mid = btn.getAttribute('data-mid');
                        try {
                            const resp = await fetch('/api/delete-generated', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filename: fname, message_id: mid}) });
                            const data = await resp.json();
                            if (resp.ok) {
                                const cell = e.target.closest('.gallery-cell');
                                cell.remove();
                                if (!galleryGrid.children.length) galleryEmpty.style.display='block';
                                // show undo toast
                                showUndoToast(fname, mid);
                            } else {
                                alert('Delete failed: ' + (data.error || resp.statusText));
                            }
                        } catch(err){ console.error(err); alert('Failed to delete'); }
                    });
                        galleryGrid.querySelectorAll('.gallery-showcase').forEach(btn=>{
                            btn.addEventListener('click', (e)=>{
                                const mid = btn.getAttribute('data-mid');
                                // open showcase modal with large image and PDF download
                                const img = btn.closest('.gallery-cell').querySelector('img');
                                const full = img.getAttribute('data-full');
                                const showcase = document.createElement('div');
                                showcase.className = 'modal-overlay';
                                showcase.style.display = 'flex';
                                showcase.innerHTML = `<div class="modal-content" style="max-width:820px;">
                                    <div class="modal-header"><h3>Showcase</h3><button class="modal-close">&times;</button></div>
                                    <div class="modal-body" style="display:flex;gap:16px;align-items:flex-start;">
                                        <div style="flex:1;">
                                            <img src="${full}" style="max-width:100%;border-radius:12px;box-shadow:var(--shadow)">
                                        </div>
                                        <div style="width:320px;display:flex;flex-direction:column;gap:12px;">
                                            <button class="btn-primary" id="download-pdf">Download PDF</button>
                                            <button class="btn-outline" id="copy-link">Copy Link</button>
                                            <button class="btn-outline" id="close-showcase">Close</button>
                                        </div>
                                    </div>
                                </div>`;
                                document.body.appendChild(showcase);
                                showcase.querySelector('.modal-close').addEventListener('click', ()=>showcase.remove());
                                showcase.querySelector('#close-showcase').addEventListener('click', ()=>showcase.remove());
                                showcase.querySelector('#copy-link').addEventListener('click', ()=>{ navigator.clipboard.writeText(full).then(()=>alert('Link copied')); });
                                showcase.querySelector('#download-pdf').addEventListener('click', ()=>{ window.open(`/api/message/report_pdf/${mid}`, '_blank'); });
                            });
                        });
                });
            } catch (err) {
                console.error('Failed to load gallery', err);
                galleryEmpty.style.display = 'block';
            }
        });
    }
    if (closeGalleryBtn) closeGalleryBtn.addEventListener('click', ()=>{ galleryModal.style.display='none'; });

    function showUndoToast(filename, messageId){
        const toast = document.createElement('div');
        toast.className = 'undo-toast';
        toast.innerHTML = `<div style="padding:10px 14px;border-radius:12px;background:var(--bg-sidebar);border:1px solid var(--border-color);box-shadow:var(--shadow);display:flex;gap:12px;align-items:center;">Deleted ${filename} <button class="btn-primary small" id="undo-btn">Undo</button></div>`;
        document.body.appendChild(toast);
        const undoBtn = toast.querySelector('#undo-btn');
        const cleanup = ()=>{ toast.remove(); };
        undoBtn.addEventListener('click', async ()=>{
            try{
                const resp = await fetch('/api/restore-generated',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename: filename, message_id: messageId})});
                const data = await resp.json();
                if (resp.ok) {
                    alert('Restored: ' + (data.restored||[]).join(', '));
                    // refresh gallery
                    openGalleryBtn.click();
                } else {
                    alert('Restore failed: ' + (data.error || resp.statusText));
                }
            }catch(err){ console.error(err); alert('Restore failed'); }
            cleanup();
        });
        // auto-dismiss after 12s
        setTimeout(cleanup, 12000);
    }

    // Camera Feature
    const cameraBtn = document.getElementById('camera-btn');
    const cameraModal = document.getElementById('camera-modal');
    const closeCameraBtn = document.getElementById('close-camera-btn');
    const cameraVideo = document.getElementById('camera-video');
    const cameraCanvas = document.getElementById('camera-canvas');
    const captureBtn = document.getElementById('capture-btn');
    let stream = null;

    if (cameraBtn) {
        cameraBtn.addEventListener('click', async () => {
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
                cameraVideo.srcObject = stream;
                cameraModal.style.display = 'flex';
            } catch (err) {
                console.error("Error accessing camera", err);
                alert("Could not access camera. Please check permissions.");
            }
        });

        const stopCamera = () => {
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                stream = null;
            }
            cameraModal.style.display = 'none';
        };

        closeCameraBtn.addEventListener('click', stopCamera);

        captureBtn.addEventListener('click', () => {
            if (!stream) return;
            // Draw video frame to canvas
            cameraCanvas.width = cameraVideo.videoWidth;
            cameraCanvas.height = cameraVideo.videoHeight;
            cameraCanvas.getContext('2d').drawImage(cameraVideo, 0, 0);
            
            // Convert to blob and set as selectedFile
            cameraCanvas.toBlob((blob) => {
                const file = new File([blob], "camera-capture.jpg", { type: "image/jpeg" });
                
                // append to selection
                selectedFiles.push(file);
                const reader = new FileReader();
                reader.onload = function(e) {
                    selectedFilePreviews.push(e.target.result);
                    addFilePreviewElement(file, e.target.result, selectedFilePreviews.length-1);
                };
                reader.readAsDataURL(file);
                
                stopCamera();
            }, 'image/jpeg', 0.8);
        });
    }

    // Drag-and-drop support onto chat area
    function handleDropFile(fileOrFiles) {
        const files = (fileOrFiles instanceof FileList || Array.isArray(fileOrFiles)) ? Array.from(fileOrFiles) : [fileOrFiles];
        files.forEach((file) => {
            if (!file || !file.type.startsWith('image/')) return;
            selectedFiles.push(file);
            const reader = new FileReader();
            reader.onload = (ev) => {
                selectedFilePreviews.push(ev.target.result);
                addFilePreviewElement(file, ev.target.result, selectedFilePreviews.length-1);
                filePreviewContainer.style.display = 'block';
            };
            reader.readAsDataURL(file);
        });
    }

    ['dragenter','dragover'].forEach(evt => {
        chatMessages.addEventListener(evt, (e)=>{ e.preventDefault(); e.stopPropagation(); chatMessages.classList.add('drag-over'); });
    });
    ['dragleave','drop'].forEach(evt => {
        chatMessages.addEventListener(evt, (e)=>{ e.preventDefault(); e.stopPropagation(); chatMessages.classList.remove('drag-over'); });
    });
    chatMessages.addEventListener('drop', (e)=>{
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length) {
            handleDropFile(dt.files);
        }
    });

    // Scan Button: send selected image + geolocation to /api/scan
    const scanBtn = document.getElementById('scan-btn');
    if (scanBtn) {
        scanBtn.addEventListener('click', async () => {
            if (!(selectedFiles && selectedFiles.length)) {
                // If user clicks hospital icon without a photo, directly find nearest veterinary hospitals
                findNearbyVets();
                return;
            }

            if (!navigator.geolocation) {
                if (!confirm('Geolocation not available. Continue without location?')) return;
            }

            scanBtn.disabled = true;
            scanBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

            const sendScan = async (lat, lon) => {
                try {
                    const formData = new FormData();
                    // use the first selected image for scan
                    formData.append('image', selectedFiles[0]);
                    if (lat && lon) {
                        formData.append('lat', lat);
                        formData.append('lon', lon);
                    }

                    const resp = await fetch('/api/scan', { method: 'POST', body: formData });
                    if (resp.status === 401) {
                        window.location.href = '/login';
                        return;
                    }
                    const data = await resp.json();
                    if (data.error) {
                        appendMessage('model', `Error: ${data.error}`);
                    } else {
                        // Show the saved image as a user message (already added earlier) and the AI raw text
                        appendMessage('model', data.scan_result || 'No analysis available', `/${data.image_path}`);

                        // If we received structured analysis, render it as cards
                        if (data.scan_analysis) {
                            try {
                                const sa = data.scan_analysis;
                                let html = '';
                                html += `<div class="scan-analysis-card">`;
                                const sev = (sa.severity||'unknown').toLowerCase();
                                const sevClass = ['low','medium','high','urgent'].includes(sev) ? sev : 'medium';
                                html += `<div class="sa-header"><strong>Analysis</strong> <span class="severity-badge ${sevClass}">${(sa.severity||'Unknown').toUpperCase()}</span> <small style="color:var(--text-secondary)">Confidence: ${Math.round((sa.confidence_overall||0)*100)}%</small></div>`;
                                if (sa.observations && sa.observations.length) {
                                    html += `<ul class="sa-observations">`;
                                    sa.observations.forEach(obs => {
                                        html += `<li><strong>${obs.label}</strong> <em>(${Math.round((obs.confidence||0)*100)}%)</em><div class="sa-desc">${obs.description||''}</div></li>`;
                                    });
                                    html += `</ul>`;
                                }
                                if (sa.recommended_action) {
                                    html += `<div class="sa-action"><strong>Recommended:</strong> ${sa.recommended_action}</div>`;
                                }
                                if (sa.tags && sa.tags.length) {
                                    html += `<div class="sa-tags">${sa.tags.map(t => `<span class="tag">${t}</span>`).join(' ')}</div>`;
                                }
                                // actions: download JSON, copy summary
                                html += `<div class="sa-actions"><button class="btn-primary small" data-action="download-json">Download report</button><button class="btn-outline" data-action="copy-summary">Copy summary</button><button class="btn-outline" data-action="share-report">Share</button></div>`;
                                html += `</div>`;
                                const msgId = appendRawMessage('model', html);
                                // Attach event handlers after DOM insertion
                                setTimeout(()=>{
                                    const container = document.getElementById(msgId);
                                    if (!container) return;
                                    const dl = container.querySelector('[data-action="download-json"]');
                                    const cp = container.querySelector('[data-action="copy-summary"]');
                                    const sh = container.querySelector('[data-action="share-report"]');
                                    if (dl) dl.addEventListener('click', ()=>{
                                        try {
                                            const imageToken = data.image_path ? data.image_path.split('/').pop() : null;
                                            if (imageToken) {
                                                // Request server PDF report; server will return 501 if libs missing
                                                const url = `/api/scan/report?image=${encodeURIComponent(imageToken)}&format=pdf`;
                                                // open in new tab to trigger download
                                                window.open(url, '_blank');
                                                return;
                                            }
                                        } catch(e){}
                                        // Fallback: download JSON client-side
                                        const blob = new Blob([JSON.stringify({scan: sa, image: data.image_path}, null, 2)], {type:'application/json'});
                                        const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'pawsense-scan-report.json'; a.click(); URL.revokeObjectURL(url);
                                    });
                                    if (cp) cp.addEventListener('click', ()=>{
                                        const summary = `Scan report — Severity: ${sa.severity||'unknown'}; Confidence: ${Math.round((sa.confidence_overall||0)*100)}%\nObservations:\n${(sa.observations||[]).map(o=>`- ${o.label}: ${o.description||''} (${Math.round((o.confidence||0)*100)}%)`).join('\n')}`;
                                        navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(summary).then(()=>alert('Summary copied')) : alert('Copy not available');
                                    });
                                    if (sh) sh.addEventListener('click', ()=>{
                                        if (navigator.share) {
                                            navigator.share({title: 'PawSense Scan Report', text: `Scan for pet — Severity: ${sa.severity||'unknown'}`, url: window.location.href}).catch(()=>{});
                                        } else { alert('Share not supported on this device'); }
                                    });
                                }, 120);
                            } catch (err) {
                                console.error('Failed to render scan_analysis', err);
                            }
                        }

                        // If hospitals found, display them using rich locator cards
                        if (data.nearby_hospitals && data.nearby_hospitals.length) {
                            const hospitalsHtml = renderNearbyVetsHtml(data.nearby_hospitals, data.maps_search_url);
                            appendRawMessage('model', hospitalsHtml);
                        } else {
                            appendMessage('model', 'No nearby veterinary clinics found for your location.');
                        }

                        // Refresh sessions/titles
                        await loadSessions();
                    }
                } catch (err) {
                    console.error('Scan error', err);
                    appendMessage('model', "Scan failed. Please try again.");
                } finally {
                    scanBtn.disabled = false;
                    scanBtn.innerHTML = '<i class="fas fa-hospital"></i>';
                }
            };

            navigator.geolocation.getCurrentPosition(async (pos) => {
                const lat = pos.coords.latitude;
                const lon = pos.coords.longitude;
                await sendScan(lat, lon);
            }, async (err) => {
                // If user denies geolocation, still send without coords
                await sendScan(null, null);
            }, { enableHighAccuracy: true, timeout: 8000 });
        });
    }

    // Pet Facts Widget
    const petFacts = [
        "A dog's sense of smell is 10,000 to 100,000 times stronger than yours!",
        "Cats have 32 muscles that control their outer ear.",
        "Dogs can understand up to 250 words and gestures.",
        "A dog's sense of smell is 10,000 to 100,000 times stronger than yours!",
        "Cats have 32 muscles that control their outer ear.",
        "Dogs can understand up to 250 words and gestures.",
        "A group of cats is called a clowder.",
        "The Basenji is the only breed of dog that can't bark.",
        "Cats sleep for about 70% of their lives.",
        "A dog's nose print is unique, much like a human fingerprint.",
        "Dalmatians are born completely white!",
        "Cats can jump up to six times their own height.",
        "A dog's heart beats 60–140 times per minute.",
        "The world's smallest dog breed is the Chihuahua.",
        "Cats purr at a frequency of 25–150 Hz, which can promote healing.",
        "Dogs have three eyelids – including a protective 'haw'.",
        "A cat's tongue has tiny backward-facing hooks to groom fur.",
        "Rabbits can see almost 360 degrees around them.",
        "Goldfish have a memory span of several months, not 3 seconds!",
    ];

    let factIndex = Math.floor(Math.random() * petFacts.length);
    const factText = document.getElementById('pet-fact-text');
    if (factText) {
        factText.textContent = petFacts[factIndex];
    }

    // Pet mini-gallery: reads from localStorage 'user_pets' (array of {name, src})
    const petGalleryRoot = document.getElementById('pet-mini-gallery');
    const loadPetGallery = () => {
        if (!petGalleryRoot) return;
        petGalleryRoot.innerHTML = '';
        let pets = [];
        try { pets = JSON.parse(localStorage.getItem('user_pets') || '[]'); } catch(e){ pets = []; }
        if (!pets || !pets.length) {
            // show placeholder add button
            const add = document.createElement('div'); add.className = 'pet-mini'; add.innerHTML = '<i class="fas fa-plus"></i>'; add.title = 'Add pet';
            add.onclick = async () => {
                const file = await new Promise(res => {
                    const inp = document.createElement('input'); inp.type='file'; inp.accept='image/*'; inp.onchange = e=>res(e.target.files[0]); inp.click();
                });
                if (file) {
                    const reader = new FileReader(); reader.onload = () => {
                        pets.push({ name: 'Pet ' + (pets.length+1), src: reader.result });
                        localStorage.setItem('user_pets', JSON.stringify(pets)); loadPetGallery();
                    }; reader.readAsDataURL(file);
                }
            };
            petGalleryRoot.appendChild(add);
            return;
        }
        pets.forEach((p, idx) => {
            const d = document.createElement('div'); d.className='pet-mini'; d.title = p.name || ('Pet ' + (idx+1));
            if (p.src) d.innerHTML = `<img src="${p.src}" alt="${p.name||'pet'}">`; else d.innerHTML = `<span class="pet-initials">${(p.name||'P').slice(0,2).toUpperCase()}</span>`;
            d.onclick = () => {
                // insert a quick prompt about this pet
                const prompt = `Please analyze the image I will upload. This is my pet: ${p.name || 'pet'}. Provide likely conditions and next steps.`;
                document.getElementById('message-input').value = prompt; document.getElementById('message-input').focus();
            };
            petGalleryRoot.appendChild(d);
        });
        // add an extra add button
        const addMore = document.createElement('div'); addMore.className='pet-mini'; addMore.innerHTML = '<i class="fas fa-plus"></i>'; addMore.title='Add pet'; addMore.onclick = ()=>{ localStorage.removeItem('user_pets'); loadPetGallery(); };
        petGalleryRoot.appendChild(addMore);
    };
    loadPetGallery();

    // Pet manager modal logic
    const petManagerModal = document.getElementById('pet-manager-modal');
    const managePetsBtn = document.getElementById('manage-pets-btn');
    const closePetManagerBtn = document.getElementById('close-pet-manager');
    const petManagerList = document.getElementById('pet-manager-list');
    const addPetBtn = document.getElementById('add-pet-btn');
    const exportPetsBtn = document.getElementById('export-pets-btn');

    function openPetManager(){
        renderPetManager();
        petManagerModal.style.display = 'flex';
    }
    function closePetManager(){ petManagerModal.style.display = 'none'; }
    if (managePetsBtn) managePetsBtn.addEventListener('click', openPetManager);
    if (closePetManagerBtn) closePetManagerBtn.addEventListener('click', closePetManager);

    function renderPetManager(){
        petManagerList.innerHTML = '';
        let pets = [];
        try { pets = JSON.parse(localStorage.getItem('user_pets') || '[]'); } catch(e){ pets = []; }
        if (!pets.length) {
            petManagerList.innerHTML = '<div style="color:var(--text-secondary)">No saved pets yet. Use Add Pet to upload a photo.</div>';
            return;
        }
        pets.forEach((p, idx) => {
            const row = document.createElement('div'); row.className='pet-manager-row';
            const imgWrap = document.createElement('div');
            imgWrap.innerHTML = p.src ? `<img src="${p.src}" alt="${p.name}">` : `<div class="pet-initials">${(p.name||'P').slice(0,2)}</div>`;
            const meta = document.createElement('div'); meta.className='pm-meta'; meta.innerHTML = `<strong>${p.name||('Pet '+(idx+1))}</strong><small style="color:var(--text-secondary)">Click rename to update the label</small>`;
            const actions = document.createElement('div'); actions.className='pm-actions';
            const renameBtn = document.createElement('button'); renameBtn.textContent='Rename';
            const deleteBtn = document.createElement('button'); deleteBtn.textContent='Delete'; deleteBtn.className='danger';
            const dlBtn = document.createElement('button'); dlBtn.textContent='Download';
            renameBtn.onclick = ()=>{
                const newName = prompt('New name for this pet', p.name||('Pet '+(idx+1)));
                if (newName!==null){ pets[idx].name = newName; localStorage.setItem('user_pets', JSON.stringify(pets)); renderPetManager(); loadPetGallery(); }
            };
            deleteBtn.onclick = ()=>{
                if (!confirm('Delete this pet?')) return; pets.splice(idx,1); localStorage.setItem('user_pets', JSON.stringify(pets)); renderPetManager(); loadPetGallery();
            };
            dlBtn.onclick = ()=>{
                if (!p.src) return; const a=document.createElement('a'); a.href=p.src; a.download=(p.name||'pet')+'.png'; a.click();
            };
            actions.appendChild(renameBtn); actions.appendChild(dlBtn); actions.appendChild(deleteBtn);
            row.appendChild(imgWrap); row.appendChild(meta); row.appendChild(actions);
            petManagerList.appendChild(row);
        });
    }

    if (addPetBtn) addPetBtn.addEventListener('click', async ()=>{
        const file = await new Promise(res => {
            const inp = document.createElement('input'); inp.type='file'; inp.accept='image/*'; inp.onchange = e=>res(e.target.files[0]); inp.click();
        });
        if (file){ const reader=new FileReader(); reader.onload=()=>{ let pets=[]; try{pets=JSON.parse(localStorage.getItem('user_pets')||'[]')}catch(e){pets=[]} pets.push({name:'Pet '+(pets.length+1), src:reader.result}); localStorage.setItem('user_pets', JSON.stringify(pets)); renderPetManager(); loadPetGallery(); }; reader.readAsDataURL(file); }
    });

    if (exportPetsBtn) exportPetsBtn.addEventListener('click', ()=>{
        const data = localStorage.getItem('user_pets') || '[]'; const blob=new Blob([data],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='pawsense-pets.json'; a.click(); URL.revokeObjectURL(a.href);
    });


    const nextFactBtn = document.getElementById('next-fact-btn');
    if (nextFactBtn && factText) {
        nextFactBtn.addEventListener('click', () => {
            factIndex = (factIndex + 1) % petFacts.length;
            factText.style.opacity = '0';
            setTimeout(() => {
                factText.textContent = petFacts[factIndex];
                factText.style.transition = 'opacity .3s';
                factText.style.opacity = '1';
            }, 200);
        });
    }

    // Quick Action Chips
    document.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const prompt = chip.getAttribute('data-prompt');
            if (prompt) setPrompt(prompt);
        });
    });

    // Global: set prompt into input
    window.setPrompt = function(text) {
        messageInput.value = text;
        messageInput.style.height = 'auto';
        messageInput.style.height = messageInput.scrollHeight + 'px';
        sendBtn.disabled = false;
        document.getElementById('input-wrapper').classList.add('is-typing');
        messageInput.focus();
    };

    // Profile Dropdown Toggle + chevron
    const profileTrigger = document.getElementById('user-profile-trigger');
    const profileDropdown = document.getElementById('profile-dropdown');
    
    // We remove the old dropdown toggling. 
    // Now clicking the user profile trigger directly opens the Profile modal.
    if (profileTrigger) {
        profileTrigger.addEventListener('click', (e) => {
            e.stopPropagation();
            openModal('profile');
        });
    }
    // Modal Logic
    const premiumModal = document.getElementById('premium-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalClose = document.getElementById('modal-close');

    if (modalClose) {
        modalClose.addEventListener('click', () => {
            premiumModal.classList.remove('active');
        });
    }

    window.openModal = function(type) {
        premiumModal.classList.add('active');
        if (profileDropdown) profileDropdown.classList.remove('active');

        if (type === 'upgrade') {
            modalTitle.textContent = 'Upgrade to Pawsense Pro';
            modalBody.innerHTML = `
                <div class="pricing-grid">
                    <div class="pricing-card">
                        <h3>Free</h3>
                        <div class="price">$0<span>/mo</span></div>
                        <ul class="feature-list">
                            <li><i class="fas fa-check"></i> Basic Pet Care Advice</li>
                            <li><i class="fas fa-check"></i> Standard Voice Mode</li>
                            <li><i class="fas fa-check"></i> Community Support</li>
                        </ul>
                        <button class="btn-primary" style="background: #475569; cursor: default;">Current Plan</button>
                    </div>
                    <div class="pricing-card premium">
                        <h3>Pro</h3>
                        <div class="price">$9.99<span>/mo</span></div>
                        <ul class="feature-list">
                            <li><i class="fas fa-check"></i> Gemini 1.5 Pro Access</li>
                            <li><i class="fas fa-check"></i> Unlimited Image Analysis</li>
                            <li><i class="fas fa-check"></i> HD Voice Mode</li>
                            <li><i class="fas fa-check"></i> Priority Expert Chat</li>
                        </ul>
                        <button class="btn-primary" onclick="alert('Proceeding to secure checkout...')">Upgrade Now</button>
                    </div>
                </div>
            `;
        } else if (type === 'personalization') {
            modalTitle.textContent = 'Personalization';
            const savedInst = localStorage.getItem('custom_instructions') || '';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px;">
                    <div class="setting-info">
                        <h4>Custom Instructions</h4>
                        <p>Tell Pawsense how you'd like it to respond (e.g., "Always be concise", "Talk like a friendly vet").</p>
                    </div>
                    <textarea class="custom-textarea" id="custom-instructions" placeholder="Enter instructions here...">${savedInst}</textarea>
                    <button class="btn-primary" id="save-instructions">Save Instructions</button>
                </div>
            `;
            setTimeout(() => {
                document.getElementById('save-instructions').addEventListener('click', () => {
                    const val = document.getElementById('custom-instructions').value;
                    localStorage.setItem('custom_instructions', val);
                    alert('Instructions saved! They will apply to new messages.');
                    premiumModal.classList.remove('active');
                });
            }, 100);
        } else if (type === 'settings') {
            modalTitle.textContent = 'Settings';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:10px;">
                    <div class="setting-item">
                        <div class="setting-info">
                            <h4>Dark Mode</h4>
                            <p>Toggle the dark and light interface.</p>
                        </div>
                        <button class="btn-primary" style="width:auto; padding: 8px 20px;" onclick="document.getElementById('theme-btn').click()">Toggle Theme</button>
                    </div>
                    <div class="dropdown-divider"></div>
                    <div class="setting-item">
                        <div class="setting-info">
                            <h4>Speech Speed</h4>
                            <p>Adjust how fast the AI reads answers.</p>
                        </div>
                        <input type="range" min="0.5" max="2" step="0.1" value="1" id="speech-speed-range">
                    </div>
                    <div class="dropdown-divider"></div>
                    <div class="setting-item" style="margin-top: 20px;">
                        <div class="setting-info">
                            <h4 style="color: #ef4444;">Danger Zone</h4>
                            <p>Permanently delete all your chat data.</p>
                        </div>
                        <button class="btn-primary" style="background:#ef4444; width:auto; padding: 8px 20px;" onclick="if(confirm('Delete ALL history? This cannot be undone.')) document.getElementById('clear-history-btn').click()">Clear All</button>
                    </div>
                </div>
            `;
        } else if (type === 'profile') {
            modalTitle.textContent = 'My Profile';
            const initials = (document.querySelector('.user-avatar')?.textContent || 'U').trim();
            const fullName = (document.querySelector('.user-full-name')?.textContent || 'User').trim();
            const savedColor = localStorage.getItem('profile_avatar_color') || 'linear-gradient(135deg,#3b82f6,#8b5cf6)';
            
            modalBody.innerHTML = `
                <div class="profile-view">
                    <div class="profile-hero">
                        <div class="profile-avatar-wrap">
                            <div class="profile-avatar-xl" id="profile-avatar-xl" style="background:${savedColor}">${initials}</div>
                            <button class="avatar-cam-btn" onclick="cycleAvatarColor()" title="Change colour"><i class="fas fa-palette"></i></button>
                        </div>
                        <div class="profile-hero-info">
                            <h2>${fullName}</h2>
                            <span class="profile-plan-badge"><i class="fas fa-star"></i> Free Plan</span>
                            <span class="profile-joined"><i class="fas fa-calendar-alt"></i> Member since May 2025</span>
                        </div>
                    </div>
                    <div class="profile-stats">
                        <div class="pstat"><i class="fas fa-comments" style="color:#3b82f6;"></i><span id="stat-chats">—</span><small>Chats</small></div>
                        <div class="pstat"><i class="fas fa-heartbeat" style="color:#ef4444;"></i><span>98%</span><small>Health Score</small></div>
                        <div class="pstat"><i class="fas fa-paw" style="color:#f59e0b;"></i><span id="stat-pets">—</span><small>Pets</small></div>
                    </div>
                    <div class="profile-tabs">
                        <button class="ptab active" onclick="switchProfileTab('info',this)"><i class="fas fa-user"></i> Info</button>
                        <button class="ptab" onclick="switchProfileTab('security',this)"><i class="fas fa-shield-alt"></i> Security</button>
                        <button class="ptab" onclick="switchProfileTab('pets',this)"><i class="fas fa-paw"></i> My Pets</button>
                        <button class="ptab" onclick="switchProfileTab('prefs',this)"><i class="fas fa-sliders-h"></i> Prefs</button>
                    </div>
                    <div class="ptab-content active" id="tab-info">
                        <div class="pfield-group"><label><i class="fas fa-user"></i> Display Name</label><input type="text" id="pf-name" value="${fullName}" placeholder="Your full name"></div>
                        <div class="pfield-group"><label><i class="fas fa-envelope"></i> Email</label><input type="email" id="pf-email" value="${localStorage.getItem('user_email')||''}" placeholder="your@email.com"></div>
                        <div class="pfield-group"><label><i class="fas fa-phone"></i> Phone</label><input type="tel" id="pf-phone" value="${localStorage.getItem('user_phone')||''}" placeholder="+91 98765 43210"></div>
                        <div class="pfield-group"><label><i class="fas fa-map-marker-alt"></i> Location</label><input type="text" id="pf-location" value="${localStorage.getItem('user_location')||''}" placeholder="City, Country"></div>
                        <div class="pfield-group"><label><i class="fas fa-pen"></i> Bio</label><textarea id="pf-bio" rows="3" placeholder="Tell us about yourself…">${localStorage.getItem('user_bio')||''}</textarea></div>
                        <button class="btn-primary" onclick="saveProfileInfo()"><i class="fas fa-save"></i> Save Changes</button>
                    </div>
                    <div class="ptab-content" id="tab-security">
                        <div class="security-item">
                            <div class="si-left"><i class="fas fa-lock" style="color:#f59e0b"></i><div><strong>Password</strong><p>Update your password</p></div></div>
                            <button class="btn-outline" onclick="showChangePasswordForm()">Change</button>
                        </div>
                        <div id="change-pw-form" style="display:none;flex-direction:column;gap:12px;margin-top:8px;">
                            <div class="pfield-group"><label>Current Password</label><input type="password" id="pw-current" placeholder="••••••••"></div>
                            <div class="pfield-group"><label>New Password</label><input type="password" id="pw-new" placeholder="Min 8 chars" oninput="updatePwStrength(this.value)"></div>
                            <div class="pfield-group"><label>Confirm Password</label><input type="password" id="pw-confirm" placeholder="••••••••"></div>
                            <div id="pw-strength"></div>
                            <button class="btn-primary" onclick="savePassword()"><i class="fas fa-key"></i> Update Password</button>
                        </div>
                        <div class="dropdown-divider" style="margin:14px 0;"></div>
                        <div class="security-item">
                            <div class="si-left"><i class="fas fa-fingerprint" style="color:#8b5cf6"></i><div><strong>Two-Factor Auth</strong><p>Extra layer of security</p></div></div>
                            <button class="btn-outline" onclick="alert('2FA setup coming soon!')">Enable</button>
                        </div>
                        <div class="security-item">
                            <div class="si-left"><i class="fas fa-bell" style="color:#3b82f6"></i><div><strong>Login Alerts</strong><p>Email on new sign-in</p></div></div>
                            <label class="toggle-switch"><input type="checkbox" id="login-alerts-tog" ${localStorage.getItem('login_alerts')==='1'?'checked':''} onchange="localStorage.setItem('login_alerts',this.checked?'1':'0')"><span class="toggle-slider"></span></label>
                        </div>
                        <div class="security-item" style="margin-top:8px;">
                            <div class="si-left"><i class="fas fa-trash-alt" style="color:#ef4444"></i><div><strong>Delete Account</strong><p>Permanently remove all data</p></div></div>
                            <button class="btn-outline" style="color:#ef4444;border-color:#ef444440;" onclick="if(confirm('Delete account? This is irreversible!')) alert('Request submitted.')">Delete</button>
                        </div>
                    </div>
                    <div class="ptab-content" id="tab-pets">
                        <div id="pets-grid" class="pets-grid"></div>
                        <button class="btn-primary" style="margin-top:12px;" onclick="addPetPrompt()"><i class="fas fa-plus"></i> Add a Pet</button>
                    </div>
                    <div class="ptab-content" id="tab-prefs">
                        <div class="pref-item"><div><strong>App Theme</strong><p>Light / Dark mode</p></div><button class="btn-outline" onclick="document.getElementById('theme-btn').click()"><i class="fas fa-moon"></i> Toggle</button></div>
                        <div class="pref-item"><div><strong>Word Streaming</strong><p>AI types word by word</p></div><label class="toggle-switch"><input type="checkbox" ${localStorage.getItem('pref_stream')!=='0'?'checked':''} onchange="localStorage.setItem('pref_stream',this.checked?'1':'0')"><span class="toggle-slider"></span></label></div>
                        <div class="pref-item"><div><strong>Sound Effects</strong><p>Play sounds on send</p></div><label class="toggle-switch"><input type="checkbox" ${localStorage.getItem('pref_sound')==='1'?'checked':''} onchange="localStorage.setItem('pref_sound',this.checked?'1':'0')"><span class="toggle-slider"></span></label></div>
                        <div class="pref-item"><div><strong>Speech Speed</strong><p>AI read-aloud rate</p></div><input type="range" min="0.5" max="2" step="0.1" value="${localStorage.getItem('speech_speed')||1}" id="speech-speed-range" oninput="localStorage.setItem('speech_speed',this.value)" style="width:100px;"></div>
                    </div>
                    <div class="upgrade-banner" onclick="openModal('upgrade')">
                        <i class="fas fa-bolt"></i>
                        <div><strong>Upgrade to Pro</strong><small>Unlock unlimited features</small></div>
                        <i class="fas fa-chevron-right"></i>
                    </div>
                </div>
            `;
            fetch('/api/sessions').then(r=>r.json()).then(s=>{const el=document.getElementById('stat-chats');if(el)el.textContent=s.length||0;}).catch(()=>{});
            loadPetsGrid();
        } else if (type === 'help') {
            modalTitle.textContent = 'How can we help?';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:30px;">
                    <!-- FAQ Categories -->
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:15px;">
                        <div class="help-card" onclick="showHelpArticle('started')">
                            <i class="fas fa-rocket" style="color:#3b82f6;"></i>
                            <h4>Getting Started</h4>
                            <p>Learn the basics of Pawsense AI</p>
                        </div>
                        <div class="help-card" onclick="showHelpArticle('scanning')">
                            <i class="fas fa-camera" style="color:#8b5cf6;"></i>
                            <h4>Image Analysis</h4>
                            <p>How to scan your pets</p>
                        </div>
                        <div class="help-card" onclick="showHelpArticle('billing')">
                            <i class="fas fa-credit-card" style="color:#f59e0b;"></i>
                            <h4>Billing & Plans</h4>
                            <p>Manage your Pro subscription</p>
                        </div>
                        <div class="help-card" onclick="showHelpArticle('privacy')">
                            <i class="fas fa-lock" style="color:#ef4444;"></i>
                            <h4>Privacy & Security</h4>
                            <p>How we protect your data</p>
                        </div>
                    </div>

                    <!-- Contact Support -->
                    <div style="text-align:center; padding:20px; border-top:1px solid var(--border-color);">
                        <p style="margin-bottom:15px;">Still need help?</p>
                        <button class="btn-primary" style="width:auto; padding:12px 30px;" onclick="window.location.href='mailto:support@pawsense.ai'">
                            <i class="fas fa-headset"></i> Contact Live Support
                        </button>
                    </div>
                </div>
            `;
        }
    }; // END OF window.openModal

    // ── Profile helper functions ──────────────────────────────────────────
    const AVATAR_COLORS = [
        'linear-gradient(135deg,#3b82f6,#8b5cf6)',
        'linear-gradient(135deg,#10b981,#3b82f6)',
        'linear-gradient(135deg,#f59e0b,#ef4444)',
        'linear-gradient(135deg,#ec4899,#8b5cf6)',
        'linear-gradient(135deg,#06b6d4,#3b82f6)',
        'linear-gradient(135deg,#84cc16,#10b981)',
    ];
    let avatarColorIdx = 0;

    window.cycleAvatarColor = function() {
        avatarColorIdx = (avatarColorIdx + 1) % AVATAR_COLORS.length;
        const color = AVATAR_COLORS[avatarColorIdx];
        const el = document.getElementById('profile-avatar-xl');
        if (el) el.style.background = color;
        localStorage.setItem('profile_avatar_color', color);
        // Also update sidebar avatar
        const sidebarAvatar = document.querySelector('.user-avatar');
        if (sidebarAvatar) sidebarAvatar.style.background = color;
    };

    window.switchProfileTab = function(tab, btn) {
        document.querySelectorAll('.ptab-content').forEach(c => c.classList.remove('active'));
        document.querySelectorAll('.ptab').forEach(b => b.classList.remove('active'));
        const el = document.getElementById('tab-' + tab);
        if (el) el.classList.add('active');
        if (btn) btn.classList.add('active');
    };

    window.saveProfileInfo = function() {
        const name = document.getElementById('pf-name')?.value?.trim();
        if (!name) { alert('Name cannot be empty!'); return; }
        localStorage.setItem('user_email', document.getElementById('pf-email')?.value || '');
        localStorage.setItem('user_phone', document.getElementById('pf-phone')?.value || '');
        localStorage.setItem('user_location', document.getElementById('pf-location')?.value || '');
        localStorage.setItem('user_bio', document.getElementById('pf-bio')?.value || '');
        const nameEl = document.querySelector('.user-full-name');
        if (nameEl) nameEl.textContent = name;
        const initials = name.split(' ').map(n => n[0]).join('').substring(0,2).toUpperCase();
        document.querySelectorAll('.user-avatar').forEach(a => { if (!a.querySelector('img')) a.textContent = initials; });
        // Flash save button
        const btn = event?.target;
        if (btn) { btn.innerHTML = '<i class="fas fa-check"></i> Saved!'; btn.style.background='#10b981'; setTimeout(()=>{ btn.innerHTML='<i class="fas fa-save"></i> Save Changes'; btn.style.background=''; }, 2000); }
    };

    window.showChangePasswordForm = function() {
        const form = document.getElementById('change-pw-form');
        if (form) { form.style.display = form.style.display === 'none' ? 'flex' : 'none'; }
    };

    window.updatePwStrength = function(val) {
        const s = document.getElementById('pw-strength');
        if (!s) return;
        if (!val) { s.innerHTML = ''; return; }
        const strong = val.length >= 10 && /[A-Z]/.test(val) && /[0-9]/.test(val);
        const medium = val.length >= 6;
        const label = strong ? 'Strong' : medium ? 'Medium' : 'Weak';
        const color = strong ? '#10b981' : medium ? '#f59e0b' : '#ef4444';
        const pct = strong ? 100 : medium ? 60 : 30;
        s.innerHTML = `<div style="height:4px;background:var(--border-color);border-radius:4px;margin:4px 0;overflow:hidden;"><div style="height:100%;width:${pct}%;background:${color};border-radius:4px;transition:width .3s;"></div></div><span style="font-size:.78rem;color:${color};">${label} password</span>`;
    };

    window.savePassword = function() {
        const cur = document.getElementById('pw-current')?.value;
        const nw = document.getElementById('pw-new')?.value;
        const conf = document.getElementById('pw-confirm')?.value;
        if (!cur || !nw) { alert('Please fill in all fields.'); return; }
        if (nw.length < 6) { alert('Password must be at least 6 characters.'); return; }
        if (nw !== conf) { alert('Passwords do not match!'); return; }
        alert('✅ Password updated successfully!');
        showChangePasswordForm();
    };

    window.loadPetsGrid = function() {
        const grid = document.getElementById('pets-grid');
        if (!grid) return;
        const pets = JSON.parse(localStorage.getItem('user_pets') || '[]');
        
        // Update pets stat
        const statPets = document.getElementById('stat-pets');
        if (statPets) statPets.textContent = pets.length;

        if (pets.length === 0) {
            grid.innerHTML = '<p style="color:var(--text-secondary);font-size:.88rem;text-align:center;padding:20px 0;">No pets added yet. Click "Add a Pet" below!</p>';
        } else {
            grid.innerHTML = pets.map((p, i) => `
                <div class="pet-card">
                    <div class="pet-icon" style="background:${AVATAR_COLORS[i % AVATAR_COLORS.length]}"><i class="fas fa-${p.type==='cat'?'cat':p.type==='bird'?'dove':p.type==='fish'?'fish':'dog'}"></i></div>
                    <div class="pet-info"><strong>${p.name}</strong><span>${p.breed || p.type}</span></div>
                    <button onclick="removePet(${i})" style="background:none;border:none;color:#ef4444;cursor:pointer;padding:4px;"><i class="fas fa-trash-alt"></i></button>
                </div>
            `).join('');
        }
    };

    window.addPetPrompt = function() {
        const name = prompt('Pet name?');
        if (!name) return;
        const type = prompt('Type (dog / cat / bird / fish / other)?') || 'dog';
        const breed = prompt('Breed (optional)?') || '';
        const pets = JSON.parse(localStorage.getItem('user_pets') || '[]');
        pets.push({ name: name.trim(), type: type.trim().toLowerCase(), breed: breed.trim() });
        localStorage.setItem('user_pets', JSON.stringify(pets));
        loadPetsGrid();
    };

    window.removePet = function(index) {
        if (!confirm('Remove this pet?')) return;
        const pets = JSON.parse(localStorage.getItem('user_pets') || '[]');
        pets.splice(index, 1);
        localStorage.setItem('user_pets', JSON.stringify(pets));
        loadPetsGrid();
    };

    window.showHelpArticle = function(category) {
        if (category === 'scanning') {
            modalTitle.textContent = 'How to Scan & Analyze Pets';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px;">
                    <div style="background:rgba(139,92,246,0.1); padding:25px; border-radius:16px; border:1px solid rgba(139,92,246,0.3);">
                        <h3 style="color:#8b5cf6; margin-bottom:15px;"><i class="fas fa-magic"></i> Step-by-Step Guide</h3>
                        <ol style="padding-left:20px; line-height:1.8; color:var(--text-primary);">
                            <li>Tap the **Image Icon** 🖼️ in the chat bar.</li>
                            <li>Take a live photo or upload one from your gallery.</li>
                            <li>Ask a specific question like "Check my dog's eyes" or "What breed is this?".</li>
                            <li>Wait a few seconds for Pawsense AI to process the image and provide expert advice.</li>
                        </ol>
                    </div>
                    <div>
                        <h4 style="margin-bottom:10px;">Supported Analysis:</h4>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                            <div style="padding:15px; background:rgba(255,255,255,0.03); border-radius:12px; border:1px solid var(--border-color);">
                                <i class="fas fa-dog" style="color:#3b82f6; margin-bottom:8px;"></i>
                                <div style="font-weight:600; font-size:0.9rem;">Breed ID</div>
                                <div style="font-size:0.8rem; color:var(--text-secondary);">Identify over 200+ breeds.</div>
                            </div>
                            <div style="padding:15px; background:rgba(255,255,255,0.03); border-radius:12px; border:1px solid var(--border-color);">
                                <i class="fas fa-heartbeat" style="color:#ef4444; margin-bottom:8px;"></i>
                                <div style="font-weight:600; font-size:0.9rem;">Health Check</div>
                                <div style="font-size:0.8rem; color:var(--text-secondary);">Visual skin & eye assessment.</div>
                            </div>
                        </div>
                    </div>
                    <button class="btn-primary" onclick="openModal('help')" style="background:transparent; border:1px solid var(--border-color); color:var(--text-primary);">
                        <i class="fas fa-arrow-left"></i> Back to Help
                    </button>
                </div>
            `;
        } else if (category === 'started') {
            modalTitle.textContent = 'Getting Started';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px;">
                    <p style="color:var(--text-secondary); line-height:1.6;">Welcome to PAWSENSE! Here's how to make the most of your AI pet companion:</p>
                    <div style="display:flex; flex-direction:column; gap:15px;">
                        <div style="display:flex; gap:15px; align-items:center;">
                            <div style="width:40px; height:40px; background:#3b82f6; border-radius:50%; display:flex; align-items:center; justify-content:center; color:white; flex-shrink:0;">1</div>
                            <div>
                                <h4 style="margin:0;">Ask Anything</h4>
                                <p style="margin:0; font-size:0.85rem; color:var(--text-secondary);">Ask about nutrition, training, or health.</p>
                            </div>
                        </div>
                        <div style="display:flex; gap:15px; align-items:center;">
                            <div style="width:40px; height:40px; background:#8b5cf6; border-radius:50%; display:flex; align-items:center; justify-content:center; color:white; flex-shrink:0;">2</div>
                            <div>
                                <h4 style="margin:0;">Voice Mode</h4>
                                <p style="margin:0; font-size:0.85rem; color:var(--text-secondary);">Click the speaker icon to hear answers read aloud.</p>
                            </div>
                        </div>
                        <div style="display:flex; gap:15px; align-items:center;">
                            <div style="width:40px; height:40px; background:#f59e0b; border-radius:50%; display:flex; align-items:center; justify-content:center; color:white; flex-shrink:0;">3</div>
                            <div>
                                <h4 style="margin:0;">Multilingual Support</h4>
                                <p style="margin:0; font-size:0.85rem; color:var(--text-secondary);">Switch to Kannada, Hindi, or other languages anytime.</p>
                            </div>
                        </div>
                    </div>
                    <button class="btn-primary" onclick="openModal('help')" style="background:transparent; border:1px solid var(--border-color); color:var(--text-primary);">
                        <i class="fas fa-arrow-left"></i> Back to Help
                    </button>
                </div>
            `;
        } else if (category === 'billing') {
            modalTitle.textContent = 'Billing & Plans';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px;">
                    <div style="background:rgba(245,158,11,0.1); padding:20px; border-radius:16px; border:1px solid rgba(245,158,11,0.3);">
                        <h4 style="color:#f59e0b;"><i class="fas fa-star"></i> Pawsense Pro Benefits</h4>
                        <ul style="margin-top:10px; padding-left:20px; line-height:1.6;">
                            <li>Unlimited high-resolution pet scans</li>
                            <li>Faster AI response times</li>
                            <li>Early access to new features</li>
                            <li>No advertisements</li>
                        </ul>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:15px;">
                        <div class="setting-item">
                            <div class="setting-info">
                                <h4>Payment Methods</h4>
                                <p>Manage your credit cards or digital wallets.</p>
                            </div>
                            <button class="btn-primary" style="width:auto; padding:8px 15px; background:transparent; border:1px solid var(--border-color);" onclick="alert('Digital payment gateway loading...')">Manage</button>
                        </div>
                        <div class="setting-item">
                            <div class="setting-info">
                                <h4>Invoice History</h4>
                                <p>View and download your past receipts.</p>
                            </div>
                            <button class="btn-primary" style="width:auto; padding:8px 15px; background:transparent; border:1px solid var(--border-color);" onclick="alert('No invoices found for this account.')">View</button>
                        </div>
                    </div>
                    <button class="btn-primary" onclick="openModal('help')" style="background:transparent; border:1px solid var(--border-color); color:var(--text-primary);">
                        <i class="fas fa-arrow-left"></i> Back to Help
                    </button>
                </div>
            `;
        } else if (category === 'privacy') {
            modalTitle.textContent = 'Privacy & Security';
            modalBody.innerHTML = `
                <div style="display:flex; flex-direction:column; gap:20px;">
                    <div style="background:rgba(16,185,129,0.1); padding:20px; border-radius:16px; border:1px solid rgba(16,185,129,0.3);">
                        <h4 style="color:#10b981;"><i class="fas fa-user-shield"></i> Your Data is Safe</h4>
                        <p style="margin-top:10px; font-size:0.9rem; line-height:1.6; color:var(--text-primary);">
                            At Pawsense, we use industry-standard **AES-256 encryption** to protect your chat history and pet images. Your data is never sold to third parties.
                        </p>
                    </div>
                    <div>
                        <h4 style="margin-bottom:15px;">Privacy FAQ</h4>
                        <div style="display:flex; flex-direction:column; gap:15px;">
                            <details style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px;">
                                <summary style="cursor:pointer; font-weight:600;">How are my pet images used?</summary>
                                <p style="padding-top:10px; font-size:0.85rem; color:var(--text-secondary);">Images are processed by our AI to provide analysis and are then stored in your private history. You can delete them at any time.</p>
                            </details>
                            <details style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px;">
                                <summary style="cursor:pointer; font-weight:600;">Can I export my data?</summary>
                                <p style="padding-top:10px; font-size:0.85rem; color:var(--text-secondary);">Yes! You can request a full export of your pet's medical chat history from the settings menu.</p>
                            </details>
                        </div>
                    </div>
                    <button class="btn-primary" onclick="openModal('help')" style="background:transparent; border:1px solid var(--border-color); color:var(--text-primary);">
                        <i class="fas fa-arrow-left"></i> Back to Help
                    </button>
                </div>
            `;
        }
    }

    // Connect Dropdown to Modal
    const dropdownList = document.querySelector('.dropdown-list');
    if (dropdownList) {
        const items = dropdownList.querySelectorAll('li');
        if (items.length >= 4) {
            items[0].addEventListener('click', () => openModal('upgrade'));
            items[1].addEventListener('click', () => openModal('personalization'));
            items[2].addEventListener('click', () => openModal('profile'));
            items[3].addEventListener('click', () => openModal('settings'));
        }
    }
    
    // Logout Functionality
    const logoutItem = document.querySelector('.dropdown-list:last-of-type li:last-child');
    if (logoutItem) {
        logoutItem.addEventListener('click', () => {
            if (confirm('Are you sure you want to log out?')) {
                window.location.href = '/logout';
            }
        });
    }

    // Photo Click Handler
    window.triggerPhotoUpload = function() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.onchange = (e) => {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    const display = document.getElementById('profile-pic-display');
                    if (display) {
                        display.innerHTML = `<img src="${event.target.result}" style="width:100%; height:100%; border-radius:50%; object-fit:cover;">`;
                    }
                    alert('Profile picture updated locally! (Backend save coming soon)');
                };
                reader.readAsDataURL(file);
            }
        };
        input.click();
    };

    // History Search Filtering
    const historySearch = document.getElementById('history-search');
    if (historySearch) {
        historySearch.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            const items = document.querySelectorAll('.history-item');
            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                if (text.includes(query)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        });
    }

    // Initialize
    loadSessions();

    // Register Service Worker for PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js')
            .then(reg => console.log('Service Worker registered', reg))
            .catch(err => console.error('Service Worker registration failed', err));
    }

    // --- Nearest Veterinary Hospital Locator & Map Modal ---
    const mapModal = document.getElementById('map-modal');
    const closeMapBtn = document.getElementById('close-map-btn');
    const mapContainer = document.getElementById('map-container');
    const mapClinicList = document.getElementById('map-clinic-list');
    const mapNotice = document.getElementById('map-notice');
    let currentHospitals = [];
    let googleMapsLoaded = false;
    let mapInstance = null;
    let markers = [];
    let infoWindow = null;
    let leafletMap = null;
    let leafletMarkers = [];

    function escapeHtml(s) {
        return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function renderNearbyVetsHtml(hospitals, mapsSearchUrl) {
        if (!hospitals || !hospitals.length) {
            const fallbackList = [
                {
                    name: "Emergency Pet Hospital & Trauma Care",
                    address: "24/7 Multi-Specialty Veterinary Emergency Center",
                    phone: "+91 99000 12586",
                    rating: 4.8,
                    specialty: "24/7 Emergency & ICU Care"
                },
                {
                    name: "Government Veterinary Super-Speciality Hospital",
                    address: "Comprehensive Animal Healthcare & Surgical Unit",
                    phone: "+91 80229 47300",
                    rating: 4.6,
                    specialty: "Inpatient Care & Diagnostics"
                },
                {
                    name: "CUPA Animal Care Hospital",
                    address: "Animal Rescue, Treatment & Vaccinations",
                    phone: "+91 80255 37575",
                    rating: 4.5,
                    specialty: "General Medicine & Outpatient"
                }
            ];

            const fallbackCards = fallbackList.map((h) => {
                const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(h.name)}`;
                return `
                    <div class="nv-clinic-card">
                        <div class="nv-clinic-info">
                            <div class="nv-clinic-name">${h.name}</div>
                            <div class="nv-clinic-addr"><i class="fas fa-map-pin" style="color:var(--primary); font-size:0.8rem;"></i> ${h.address}</div>
                            <div class="nv-clinic-meta">
                                <span class="nv-badge rating">⭐ ${h.rating}</span>
                                <span class="nv-badge phone"><i class="fas fa-phone"></i> ${h.phone}</span>
                                <span class="nv-badge" style="background:rgba(99,102,241,0.12); color:#6366f1;"><i class="fas fa-stethoscope"></i> ${h.specialty}</span>
                            </div>
                        </div>
                        <div class="nv-clinic-actions">
                            <a href="${mapsUrl}" target="_blank" class="nv-action-btn directions-btn" title="Find on Google Maps">
                                <i class="fas fa-directions"></i> Go
                            </a>
                        </div>
                    </div>
                `;
            }).join('');

            return `
                <div class="nearby-vets-container">
                    <div class="nearby-vets-header">
                        <div class="nv-title"><i class="fas fa-hospital-alt"></i> Veterinary Hospitals & Emergency Centers</div>
                        <div class="nv-subtitle">Top verified emergency animal clinics near you</div>
                    </div>
                    <div class="nearby-vets-list">
                        ${fallbackCards}
                    </div>
                    <div class="nearby-vets-footer">
                        <a href="${mapsSearchUrl || 'https://www.google.com/maps/search/veterinary+hospital/'}" target="_blank" class="btn-primary small" style="display:inline-flex; align-items:center; gap:6px;">
                            <i class="fab fa-google"></i> Open Full Google Maps Search
                        </a>
                    </div>
                </div>
            `;
        }

        const cardsHtml = hospitals.map((h, idx) => {
            const nameEsc = escapeHtml(h.name);
            const addrEsc = escapeHtml(h.address || 'Address available on map');
            const distBadge = h.distance_km ? `<span class="nv-badge distance"><i class="fas fa-location-arrow"></i> ${h.distance_km} km away</span>` : '';
            const ratingBadge = h.rating ? `<span class="nv-badge rating">⭐ ${h.rating}</span>` : '';
            const phoneBadge = h.phone ? `<a href="tel:${h.phone}" class="nv-badge phone"><i class="fas fa-phone"></i> ${h.phone}</a>` : '';
            const specialtyBadge = h.specialty ? `<span class="nv-badge" style="background:rgba(99,102,241,0.12); color:#6366f1;"><i class="fas fa-stethoscope"></i> ${escapeHtml(h.specialty)}</span>` : '';
            const dirUrl = h.directions_url || `https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lng}`;
            const mapsUrl = h.maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(h.name + ' ' + (h.address || ''))}`;

            return `
                <div class="nv-clinic-card" data-idx="${idx}" data-lat="${h.lat}" data-lng="${h.lng}" data-name="${nameEsc}" data-address="${addrEsc}" data-dist="${h.distance_km || ''}" data-dir="${dirUrl}" data-maps="${mapsUrl}">
                    <div class="nv-clinic-info">
                        <div class="nv-clinic-name">${nameEsc}</div>
                        <div class="nv-clinic-addr"><i class="fas fa-map-pin" style="color:var(--primary); font-size:0.8rem;"></i> ${addrEsc}</div>
                        <div class="nv-clinic-meta">
                            ${distBadge}
                            ${ratingBadge}
                            ${phoneBadge}
                            ${specialtyBadge}
                        </div>
                    </div>
                    <div class="nv-clinic-actions">
                        <button type="button" class="nv-action-btn view-map-btn" data-idx="${idx}" title="View on interactive map">
                            <i class="fas fa-map-marked-alt"></i> Map
                        </button>
                        <a href="${dirUrl}" target="_blank" class="nv-action-btn directions-btn" title="Navigate with Google Maps">
                            <i class="fas fa-directions"></i> Go
                        </a>
                    </div>
                </div>
            `;
        }).join('');

        return `
            <div class="nearby-vets-container">
                <div class="nearby-vets-header">
                    <div class="nv-title"><i class="fas fa-hospital-alt"></i> Nearest Veterinary Hospitals</div>
                    <div class="nv-subtitle">Found ${hospitals.length} clinic${hospitals.length > 1 ? 's' : ''} near your location</div>
                </div>
                <div class="nearby-vets-list">
                    ${cardsHtml}
                </div>
                <div class="nearby-vets-footer">
                    <button type="button" class="btn-primary small open-all-map-modal-btn">
                        <i class="fas fa-map-marked-alt"></i> View on Interactive Map
                    </button>
                    ${mapsSearchUrl ? `<a href="${mapsSearchUrl}" target="_blank" class="btn-outline small"><i class="fab fa-google"></i> Open in Google Maps</a>` : ''}
                </div>
            </div>
        `;
    }

    async function findNearbyVets() {
        if (!currentSessionId) {
            await createNewSession();
        }

        // Add user prompt to chat
        appendMessage('user', '🏥 Find nearest veterinary hospitals based on my location');

        const loadingMsgId = appendRawMessage('model', `
            <div class="vet-loading-msg" id="vet-loading-indicator">
                <i class="fas fa-spinner fa-spin"></i>
                <span>Getting your location & searching nearest veterinary clinics...</span>
            </div>
        `);

        const removeLoading = () => {
            const el = document.getElementById(loadingMsgId);
            if (el) el.remove();
            else {
                const ind = document.getElementById('vet-loading-indicator');
                if (ind && ind.closest('.message')) ind.closest('.message').remove();
            }
        };

        const fetchClinics = async (lat, lon) => {
            try {
                let url = '/api/nearby-vets';
                if (lat != null && lon != null) {
                    url += `?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`;
                }
                const resp = await fetch(url);
                removeLoading();

                if (resp.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                const data = await resp.json();
                if (data.error && (!data.nearby_hospitals || !data.nearby_hospitals.length)) {
                    appendMessage('model', `Location lookup: ${data.error}`);
                    return;
                }

                const hospitals = data.nearby_hospitals || [];
                const html = renderNearbyVetsHtml(hospitals, data.maps_search_url);
                appendRawMessage('model', html);

                // Auto-open map modal if hospitals found
                if (hospitals.length > 0) {
                    openMapModal(hospitals, 0);
                }
                await loadSessions();
            } catch (err) {
                removeLoading();
                console.error('Error finding vets:', err);
                appendMessage('model', 'Failed to retrieve nearby clinics. Please check your network connection or location permissions.');
            }
        };

        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    fetchClinics(pos.coords.latitude, pos.coords.longitude);
                },
                (err) => {
                    console.warn('Browser geolocation failed/denied, falling back to IP lookup:', err);
                    fetchClinics(null, null);
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
            );
        } else {
            fetchClinics(null, null);
        }
    }

    // Attach click listeners to all vet locator entry points
    const findVetsBtn = document.getElementById('find-vets-btn');
    if (findVetsBtn) findVetsBtn.addEventListener('click', findNearbyVets);

    const chipFindVets = document.getElementById('chip-find-vets');
    if (chipFindVets) chipFindVets.addEventListener('click', findNearbyVets);

    const cardFindVets = document.getElementById('card-find-vets');
    if (cardFindVets) cardFindVets.addEventListener('click', findNearbyVets);

    function loadGoogleMaps() {
        return new Promise((resolve, reject) => {
            if (googleMapsLoaded || !window.GOOGLE_BROWSER_KEY) return resolve();
            const script = document.createElement('script');
            script.src = `https://maps.googleapis.com/maps/api/js?key=${window.GOOGLE_BROWSER_KEY}`;
            script.async = true;
            script.defer = true;
            script.onload = () => { googleMapsLoaded = true; resolve(); };
            script.onerror = (e) => reject(e);
            document.head.appendChild(script);
        });
    }

    function renderLeafletMap(hospitals, startIndex) {
        if (typeof L === 'undefined') {
            console.warn('Leaflet library is not available');
            return;
        }

        if (mapNotice) {
            if (!window.GOOGLE_BROWSER_KEY) {
                mapNotice.style.display = 'block';
                mapNotice.innerHTML = `<i class="fas fa-info-circle"></i> Showing interactive map via OpenStreetMap. To enable Google Maps view, add <code>GOOGLE_BROWSER_KEY</code> in your .env or Vercel environment variables.`;
            } else {
                mapNotice.style.display = 'none';
            }
        }

        const first = hospitals[startIndex] || hospitals[0];
        const centerLat = parseFloat(first.lat) || 0;
        const centerLng = parseFloat(first.lng) || 0;

        if (!leafletMap) {
            mapContainer.innerHTML = '';
            leafletMap = L.map(mapContainer).setView([centerLat, centerLng], 14);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            }).addTo(leafletMap);
        } else {
            leafletMarkers.forEach(m => leafletMap.removeLayer(m));
            leafletMarkers = [];
        }

        const bounds = [];
        hospitals.forEach((h, i) => {
            const lat = parseFloat(h.lat);
            const lng = parseFloat(h.lng);
            if (isNaN(lat) || isNaN(lng)) return;
            const latlng = [lat, lng];
            bounds.push(latlng);

            const marker = L.marker(latlng).addTo(leafletMap);
            const dirLink = h.directions_url || `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
            const gmapsLink = h.maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(h.name + ' ' + (h.address || ''))}`;

            marker.bindPopup(`
                <div style="min-width:180px; font-family:inherit;">
                    <strong style="font-size:0.95rem;color:#1e293b;display:block;margin-bottom:4px;">${escapeHtml(h.name)}</strong>
                    <div style="font-size:0.82rem;color:#64748b;margin-bottom:6px;">${escapeHtml(h.address || '')}</div>
                    ${h.distance_km ? `<div style="font-size:0.82rem;color:var(--primary,#4f46e5);font-weight:600;margin-bottom:6px;">📍 ${h.distance_km} km away</div>` : ''}
                    <div style="display:flex; gap:6px; margin-top:6px;">
                        <a href="${dirLink}" target="_blank" style="padding:4px 8px;background:#4f46e5;color:#fff;border-radius:6px;font-size:0.78rem;text-decoration:none;font-weight:600;">🚗 Directions</a>
                        <a href="${gmapsLink}" target="_blank" style="padding:4px 8px;background:#f1f5f9;color:#334155;border-radius:6px;font-size:0.78rem;text-decoration:none;font-weight:600;">Google Maps</a>
                    </div>
                </div>
            `);
            leafletMarkers.push(marker);

            if (i === startIndex) {
                marker.openPopup();
                leafletMap.setView(latlng, 15);
            }
        });

        if (bounds.length > 0 && startIndex === 0) {
            leafletMap.fitBounds(bounds, { padding: [35, 35] });
        }

        setTimeout(() => {
            if (leafletMap) leafletMap.invalidateSize();
        }, 250);
    }

    async function openMapModal(hospitals, startIndex = 0) {
        if (!hospitals || hospitals.length === 0) return;
        currentHospitals = hospitals;
        mapClinicList.innerHTML = '';

        hospitals.forEach((h, i) => {
            const item = document.createElement('div');
            item.className = 'map-clinic-item' + (i === startIndex ? ' active' : '');
            const distText = h.distance_km ? `<span style="font-size:0.78rem;color:var(--primary);font-weight:600;"><i class="fas fa-location-arrow"></i> ${h.distance_km} km</span>` : '';
            const ratingText = h.rating ? `<span style="font-size:0.78rem;color:#f59e0b;">⭐ ${h.rating}</span>` : '';
            item.innerHTML = `
                <strong style="display:block;margin-bottom:2px;">${escapeHtml(h.name)}</strong>
                <div style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:4px;">${escapeHtml(h.address || '')}</div>
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    ${distText}
                    ${ratingText}
                </div>
            `;
            item.addEventListener('click', () => {
                document.querySelectorAll('.map-clinic-item').forEach(el => el.classList.remove('active'));
                item.classList.add('active');

                if (googleMapsLoaded && markers[i] && mapInstance) {
                    mapInstance.panTo(markers[i].getPosition());
                    mapInstance.setZoom(15);
                    google.maps.event.trigger(markers[i], 'click');
                } else if (leafletMap && leafletMarkers[i]) {
                    const lat = parseFloat(h.lat);
                    const lng = parseFloat(h.lng);
                    if (!isNaN(lat) && !isNaN(lng)) {
                        leafletMap.setView([lat, lng], 15);
                        leafletMarkers[i].openPopup();
                    }
                }
            });
            mapClinicList.appendChild(item);
        });

        mapModal.style.display = 'flex';

        if (window.GOOGLE_BROWSER_KEY) {
            try {
                await loadGoogleMaps();
                if (mapNotice) mapNotice.style.display = 'none';
                const first = hospitals[startIndex] || hospitals[0];
                const center = { lat: parseFloat(first.lat) || 0, lng: parseFloat(first.lng) || 0 };
                mapInstance = new google.maps.Map(mapContainer, { center, zoom: 14 });
                infoWindow = new google.maps.InfoWindow();

                markers.forEach(m => m.setMap(null));
                markers = [];
                hospitals.forEach((h, i) => {
                    const pos = { lat: parseFloat(h.lat), lng: parseFloat(h.lng) };
                    const marker = new google.maps.Marker({ position: pos, map: mapInstance, title: h.name });
                    const dirLink = h.directions_url || `https://www.google.com/maps/dir/?api=1&destination=${pos.lat},${pos.lng}`;
                    marker.addListener('click', () => {
                        infoWindow.setContent(`
                            <div style="min-width:180px;padding:4px;">
                                <strong>${escapeHtml(h.name)}</strong>
                                <div style="font-size:.85rem;color:#555;margin:4px 0;">${escapeHtml(h.address || '')}</div>
                                ${h.distance_km ? `<div style="font-size:.85rem;color:#4f46e5;font-weight:600;">📍 ${h.distance_km} km away</div>` : ''}
                                ${h.rating ? `<div>⭐ ${h.rating}</div>` : ''}
                                <div style="margin-top:6px;">
                                    <a href="${dirLink}" target="_blank" style="padding:4px 8px;background:#4f46e5;color:#fff;border-radius:4px;font-size:0.8rem;text-decoration:none;">🚗 Directions</a>
                                </div>
                            </div>
                        `);
                        infoWindow.open(mapInstance, marker);
                    });
                    markers.push(marker);
                });

                if (markers[startIndex]) google.maps.event.trigger(markers[startIndex], 'click');
            } catch (err) {
                console.warn('Google Maps JS loading failed, falling back to Leaflet:', err);
                renderLeafletMap(hospitals, startIndex);
            }
        } else {
            renderLeafletMap(hospitals, startIndex);
        }
    }

    if (closeMapBtn) closeMapBtn.addEventListener('click', () => { mapModal.style.display = 'none'; });

    // Delegate clicks inside chat for vet cards and buttons
    document.addEventListener('click', (e) => {
        // 1. Click on "View on Interactive Map" in footer
        const openAllBtn = e.target.closest && e.target.closest('.open-all-map-modal-btn');
        if (openAllBtn) {
            e.preventDefault();
            const container = openAllBtn.closest('.nearby-vets-container');
            if (!container) return;
            const cards = Array.from(container.querySelectorAll('.nv-clinic-card'));
            const hospitals = cards.map(c => ({
                name: c.dataset.name,
                address: c.dataset.address,
                lat: c.dataset.lat,
                lng: c.dataset.lng,
                distance_km: c.dataset.dist,
                directions_url: c.dataset.dir,
                maps_url: c.dataset.maps
            }));
            openMapModal(hospitals, 0);
            return;
        }

        // 2. Click on "Map" button on a specific clinic card
        const viewMapBtn = e.target.closest && e.target.closest('.view-map-btn');
        if (viewMapBtn) {
            e.preventDefault();
            const card = viewMapBtn.closest('.nv-clinic-card');
            const container = viewMapBtn.closest('.nearby-vets-container');
            if (!card || !container) return;
            const cards = Array.from(container.querySelectorAll('.nv-clinic-card'));
            const hospitals = cards.map(c => ({
                name: c.dataset.name,
                address: c.dataset.address,
                lat: c.dataset.lat,
                lng: c.dataset.lng,
                distance_km: c.dataset.dist,
                directions_url: c.dataset.dir,
                maps_url: c.dataset.maps
            }));
            const idx = parseInt(card.dataset.idx || '0', 10) || 0;
            openMapModal(hospitals, idx);
            return;
        }

        // 3. Click on the clinic card itself (excluding links and action buttons)
        const clinicCard = e.target.closest && e.target.closest('.nv-clinic-card');
        if (clinicCard && !e.target.closest('a') && !e.target.closest('button')) {
            const container = clinicCard.closest('.nearby-vets-container');
            if (!container) return;
            const cards = Array.from(container.querySelectorAll('.nv-clinic-card'));
            const hospitals = cards.map(c => ({
                name: c.dataset.name,
                address: c.dataset.address,
                lat: c.dataset.lat,
                lng: c.dataset.lng,
                distance_km: c.dataset.dist,
                directions_url: c.dataset.dir,
                maps_url: c.dataset.maps
            }));
            const idx = parseInt(clinicCard.dataset.idx || '0', 10) || 0;
            openMapModal(hospitals, idx);
            return;
        }

        // 4. Backwards compatibility for .hospital-item
        const el = e.target.closest && e.target.closest('.hospital-item');
        if (el) {
            e.preventDefault();
            const msg = el.closest('.nearby-hospitals');
            if (!msg) return;
            const items = Array.from(msg.querySelectorAll('.hospital-item'));
            const hospitals = items.map(it => ({
                name: it.dataset.name,
                address: it.dataset.address,
                lat: it.dataset.lat,
                lng: it.dataset.lng,
                rating: it.dataset.rating
            }));
            const idx = parseInt(el.dataset.idx || '0', 10) || 0;
            openMapModal(hospitals, idx);
        }
    });
});
