let currentSessionId = null;
let currentTerminal = null;
let sessions = {};
let outputPollingInterval = null;
let fitAddon = null;
let currentTool = null;
let currentPath = '/';
let isLoadingFiles = false;

// Tool panel functions
function openTool(toolName) {
    // Check if we have an active session
    if (!currentSessionId || !sessions[currentSessionId]) {
        alert('Please connect to a server first');
        return;
    }
    
    // Close current tool if clicking the same icon
    if (currentTool === toolName) {
        closeToolPanel();
        return;
    }
    
    // Reset all tool icons
    document.querySelectorAll('.tool-icon').forEach(icon => {
        icon.classList.remove('active');
    });
    
    // Hide all tool panels
    document.querySelectorAll('.tool-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    
    // Open the selected tool
    currentTool = toolName;
    document.getElementById(toolName + 'Icon').classList.add('active');
    document.getElementById(toolName + 'Panel').classList.add('active');
    document.getElementById('rightSidebar').classList.add('open');
    
    // Initialize tool based on type
    if (toolName === 'sftp') {
        initializeSFTP();
    }
    
    // Resize terminal after sidebar opens
    setTimeout(() => {
        if (sessions[currentSessionId]?.calculateSize) {
            sessions[currentSessionId].calculateSize();
        }
    }, 350); // After animation completes
}

function closeToolPanel() {
    currentTool = null;
    document.getElementById('rightSidebar').classList.remove('open');
    document.querySelectorAll('.tool-icon').forEach(icon => {
        icon.classList.remove('active');
    });
    document.querySelectorAll('.tool-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    
    // Resize terminal after sidebar closes
    setTimeout(() => {
        if (sessions[currentSessionId]?.calculateSize) {
            sessions[currentSessionId].calculateSize();
        }
    }, 350);
}

// SFTP Functions
async function initializeSFTP() {
    currentPath = '/home/' + sessions[currentSessionId].username;
    document.getElementById('currentPath').textContent = currentPath;
    await listFiles(currentPath);
}

async function listFiles(path) {
    // Prevent multiple simultaneous requests
    if (isLoadingFiles) {
        console.log('Already loading files, please wait...');
        return;
    }
    
    isLoadingFiles = true;
    const fileList = document.getElementById('fileList');
    const loadingIndicator = document.getElementById('fileBrowserLoading');
    
    // Show loading state
    fileList.classList.add('loading');
    loadingIndicator.classList.add('active');
    
    try {
        const result = JSON.parse(
            await window.pywebview.api.list_directory(currentSessionId, path)
        );
        
        if (!result.success) {
            console.error('Failed to list directory:', result.error);
            fileList.innerHTML = '<div class="empty-message">Error loading files</div>';
            return;
        }
        
        fileList.innerHTML = '';
        
        // Add parent directory if not at root
        if (path !== '/') {
            const parentItem = document.createElement('div');
            parentItem.className = 'file-item';
            parentItem.innerHTML = `
                <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
                <span class="file-name">..</span>
                <span class="file-size">-</span>
                <span class="file-date">-</span>
            `;
            parentItem.ondblclick = () => {
                if (!isLoadingFiles) navigateUp();
            };
            parentItem.onclick = () => selectFile(parentItem);
            fileList.appendChild(parentItem);
        }
        
        // Add files and directories
        result.files.forEach(file => {
            const item = document.createElement('div');
            item.className = 'file-item';
            item.innerHTML = `
                <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    ${file.type === 'directory' ? 
                        '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>' :
                        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'
                    }
                </svg>
                <span class="file-name" title="${file.name}">${file.name}</span>
                <span class="file-size">${file.size}</span>
                <span class="file-date">${file.date}</span>
            `;
            
            if (file.type === 'directory') {
                item.ondblclick = () => {
                    if (!isLoadingFiles) navigateToFolder(file.name);
                };
            } else {
                item.ondblclick = () => {
                    if (!isLoadingFiles) downloadFile(file.name);
                };
            }
            
            item.onclick = () => selectFile(item);
            fileList.appendChild(item);
        });
        
        // Scroll to top after loading
        fileList.scrollTop = 0;
        
    } catch (error) {
        console.error('Error listing files:', error);
        fileList.innerHTML = '<div class="empty-message">Error loading files</div>';
    } finally {
        // Hide loading state
        isLoadingFiles = false;
        fileList.classList.remove('loading');
        loadingIndicator.classList.remove('active');
    }
}

function selectFile(element) {
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
    });
    element.classList.add('selected');
}

function navigateUp() {
    if (isLoadingFiles) return;
    
    const parts = currentPath.split('/').filter(p => p);
    parts.pop();
    currentPath = '/' + parts.join('/');
    if (currentPath === '/') currentPath = '/';
    document.getElementById('currentPath').textContent = currentPath;
    listFiles(currentPath);
}

function navigateToFolder(folderName) {
    if (isLoadingFiles) return;
    
    currentPath = currentPath.endsWith('/') ? 
        currentPath + folderName : 
        currentPath + '/' + folderName;
    document.getElementById('currentPath').textContent = currentPath;
    listFiles(currentPath);
}

function refreshFiles() {
    if (isLoadingFiles) return;
    listFiles(currentPath);
}

function createNewFolder() {
    const folderName = prompt('Enter folder name:');
    if (folderName) {
        const fullPath = currentPath.endsWith('/') ? 
            currentPath + folderName : 
            currentPath + '/' + folderName;
        
        window.pywebview.api.create_directory(currentSessionId, fullPath).then(result => {
            const res = JSON.parse(result);
            if (res.success) {
                refreshFiles();
            } else {
                alert('Failed to create folder');
            }
        });
    }
}

async function downloadFile(fileName) {
    const remotePath = currentPath.endsWith('/') ? 
        currentPath + fileName : 
        currentPath + '/' + fileName;
    
    // For now, just log - you'd need to implement file save dialog
    console.log('Downloading:', remotePath);
    alert('File download functionality will be implemented with file save dialog');
}

function selectFiles() {
    document.getElementById('fileInput').click();
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        // Upload each file
        Array.from(files).forEach(file => {
            const remotePath = currentPath.endsWith('/') ? 
                currentPath + file.name : 
                currentPath + '/' + file.name;
            
            console.log('Uploading:', file.name, 'to', remotePath);
            // You'd need to implement actual file upload here
            alert('File upload functionality will be implemented');
        });
    }
}

// Drag and drop support
const setupDragDrop = () => {
    const uploadArea = document.getElementById('uploadArea');
    if (!uploadArea) return;
    
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            console.log('Dropped files:', files);
            // Implement SFTP upload
        }
    });
};

// Toggle collapsible sections
function toggleSection(sectionName) {
    const content = document.getElementById(sectionName + 'Content');
    const chevron = document.getElementById(sectionName + 'Chevron');
    
    content.classList.toggle('open');
    chevron.classList.toggle('open');
}

// Load saved connections on startup
async function loadSavedConnections() {
    try {
        console.log('Calling get_saved_connections...');
        const response = await window.pywebview.api.get_saved_connections();
        console.log('Response from get_saved_connections:', response);
        const connections = JSON.parse(response);
        console.log('Parsed connections:', connections);
        updateSavedConnectionsList(connections);
    } catch (error) {
        console.error('Error loading saved connections:', error);
    }
}

function updateSavedConnectionsList(connections) {
    const container = document.getElementById('savedConnectionsList');
    container.innerHTML = '';
    
    if (connections.length === 0) {
        container.innerHTML = '<div class="empty-message">No saved connections</div>';
        return;
    }
    
    connections.forEach(conn => {
        const item = document.createElement('div');
        item.className = 'saved-connection-item';
        item.innerHTML = `
            <div class="saved-connection-info" onclick="loadConnection('${conn.key}')">
                <div class="saved-connection-name">${conn.name || conn.key}</div>
                <div class="saved-connection-details">${conn.hostname}:${conn.port}</div>
            </div>
            <div class="saved-connection-actions">
                <button class="action-btn" onclick="quickConnect('${conn.key}'); event.stopPropagation();">Connect</button>
                <button class="action-btn delete" onclick="deleteConnection('${conn.key}'); event.stopPropagation();">Delete</button>
            </div>
        `;
        container.appendChild(item);
    });
}

async function loadConnection(key) {
    const connections = JSON.parse(await window.pywebview.api.get_saved_connections());
    const conn = connections.find(c => c.key === key);
    if (conn) {
        document.getElementById('hostname').value = conn.hostname;
        document.getElementById('port').value = conn.port || 22;
        document.getElementById('username').value = conn.username;
        
        if (conn.password) {
            document.getElementById('authType').value = 'password';
            document.getElementById('password').value = conn.password;
            document.getElementById('passwordGroup').style.display = 'block';
            document.getElementById('keyGroup').style.display = 'none';
        } else if (conn.keyPath) {
            document.getElementById('authType').value = 'key';
            document.getElementById('keyPath').value = conn.keyPath;
            document.getElementById('passwordGroup').style.display = 'none';
            document.getElementById('keyGroup').style.display = 'block';
        }
    }
}

async function quickConnect(key) {
    await loadConnection(key);
    await connect();
}

async function deleteConnection(key) {
    if (confirm('Are you sure you want to delete this saved connection?')) {
        await window.pywebview.api.delete_saved_connection(key);
        await loadSavedConnections();
    }
}

// Wait for page to load
window.addEventListener('DOMContentLoaded', () => {
    console.log('Page loaded, checking Terminal availability...');
    
    // Check if Terminal is available
    if (typeof Terminal === 'undefined') {
        console.error('Terminal library not loaded!');
        alert('Error: Terminal library failed to load. Please check your internet connection and refresh the page.');
        return;
    }
    
    console.log('Terminal library loaded successfully');
    
    // Wait for pywebview API to be ready
    function waitForAPI() {
        if (window.pywebview && window.pywebview.api) {
            console.log('PyWebView API ready, loading saved connections...');
            // Load saved connections
            loadSavedConnections();
            // Setup drag and drop
            setupDragDrop();
        } else {
            console.log('Waiting for PyWebView API...');
            setTimeout(waitForAPI, 100);
        }
    }
    
    waitForAPI();
    
    // Handle auth type change
    document.getElementById('authType').addEventListener('change', (e) => {
        if (e.target.value === 'password') {
            document.getElementById('passwordGroup').style.display = 'block';
            document.getElementById('keyGroup').style.display = 'none';
        } else {
            document.getElementById('passwordGroup').style.display = 'none';
            document.getElementById('keyGroup').style.display = 'block';
        }
    });
});

async function connect() {
    const hostname = document.getElementById('hostname').value;
    const port = document.getElementById('port').value || 22;
    const username = document.getElementById('username').value;
    const authType = document.getElementById('authType').value;
    const password = authType === 'password' ? document.getElementById('password').value : null;
    const keyPath = authType === 'key' ? document.getElementById('keyPath').value : null;
    const saveConnection = document.getElementById('saveConnection').checked;
    
    if (!hostname || !username) {
        alert('Please fill in hostname and username');
        return;
    }
    
    // Show connecting screen
    document.getElementById('welcomeScreen').style.display = 'none';
    document.getElementById('connectingScreen').style.display = 'block';
    
    try {
        // Create new session
        const sessionId = await window.pywebview.api.create_session();
        
        // Connect
        const connectionParams = JSON.stringify({
            hostname,
            port: parseInt(port),
            username,
            password,
            keyPath,
            save: saveConnection
        });
        
        const result = JSON.parse(await window.pywebview.api.connect(sessionId, connectionParams));
        
        if (result.success) {
            // Create terminal
            createTerminalForSession(sessionId, hostname);
            
            // Add to sessions list
            sessions[sessionId] = {
                id: sessionId,
                hostname,
                username,
                terminal: currentTerminal
            };
            
            updateSessionsList();
            switchToSession(sessionId);
            
            // Start polling for output
            startOutputPolling(sessionId);
            
            // Reload saved connections if a new one was saved
            if (saveConnection) {
                await loadSavedConnections();
            }
        } else {
            alert('Connection failed: ' + (result.error || 'Unknown error'));
            document.getElementById('connectingScreen').style.display = 'none';
            document.getElementById('welcomeScreen').style.display = 'flex';
        }
    } catch (error) {
        alert('Connection error: ' + error);
        document.getElementById('connectingScreen').style.display = 'none';
        document.getElementById('welcomeScreen').style.display = 'flex';
    }
}

function createTerminalForSession(sessionId, hostname) {
    try {
        // Clear the terminal container first
        const terminalElement = document.getElementById('terminal');
        terminalElement.innerHTML = '';
        
        const terminal = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily: 'Consolas, "Courier New", monospace',
            theme: {
                background: '#000000',
                foreground: '#ffffff',
                cursor: '#00ff88'
            },
            scrollback: 10000,
            convertEol: true,
            windowsMode: true
        });
        
        // Create fit addon
        let terminalFitAddon = null;
        if (typeof FitAddon !== 'undefined') {
            terminalFitAddon = new FitAddon.FitAddon();
            terminal.loadAddon(terminalFitAddon);
        }
        
        // Open terminal
        terminal.open(terminalElement);
        
        // Get container dimensions and calculate rows/cols
        const calculateTerminalSize = () => {
            const wrapper = document.getElementById('terminalWrapper');
            if (!wrapper) return;
            
            const rect = wrapper.getBoundingClientRect();
            const padding = 16; // Account for padding
            const availableHeight = rect.height - padding * 2;
            const availableWidth = rect.width - padding * 2;
            
            // Estimate character dimensions
            const charHeight = 17; // Approximate line height for 14px font
            const charWidth = 8; // Approximate char width
            
            const rows = Math.floor(availableHeight / charHeight);
            const cols = Math.floor(availableWidth / charWidth);
            
            console.log(`Terminal size: ${cols}x${rows} (${availableWidth}x${availableHeight}px)`);
            
            // Manually resize terminal
            if (rows > 0 && cols > 0) {
                terminal.resize(cols, rows);
            }
            
            // Then try fit addon
            if (terminalFitAddon) {
                try {
                    terminalFitAddon.fit();
                } catch (e) {
                    console.error('Fit addon error:', e);
                }
            }
        };
        
        // Calculate size after delays
        setTimeout(calculateTerminalSize, 50);
        setTimeout(calculateTerminalSize, 200);
        setTimeout(calculateTerminalSize, 500);
        
        terminal.focus();
        
        // Handle input
        terminal.onData(async (data) => {
            await window.pywebview.api.send_input(sessionId, data);
        });
        
        // Handle resize
        terminal.onResize(async ({ cols, rows }) => {
            console.log(`Terminal resized to ${cols}x${rows}`);
            await window.pywebview.api.resize_terminal(sessionId, cols, rows);
        });
        
        currentTerminal = terminal;
        sessions[sessionId] = { 
            ...sessions[sessionId], 
            terminal, 
            fitAddon: terminalFitAddon,
            calculateSize: calculateTerminalSize
        };
        
        // Set up resize observer
        const resizeObserver = new ResizeObserver(() => {
            if (currentSessionId === sessionId) {
                calculateTerminalSize();
            }
        });
        
        const wrapper = document.getElementById('terminalWrapper');
        if (wrapper) {
            resizeObserver.observe(wrapper);
        }
        
    } catch (error) {
        console.error('Error creating terminal:', error);
        alert('Failed to create terminal: ' + error.message);
    }
}

async function startOutputPolling(sessionId) {
    if (outputPollingInterval) {
        clearInterval(outputPollingInterval);
    }
    
    outputPollingInterval = setInterval(async () => {
        if (currentSessionId === sessionId) {
            try {
                // Get output
                const result = JSON.parse(await window.pywebview.api.get_output(sessionId));
                if (result.output) {
                    currentTerminal.write(result.output);
                }
                
                // Check session status
                const statusResult = JSON.parse(await window.pywebview.api.get_status(sessionId));
                if (!statusResult.connected) {
                    // Session disconnected
                    console.log(`Session ${sessionId} disconnected`);
                    handleSessionDisconnect(sessionId, false);
                }
            } catch (error) {
                console.error('Error polling output:', error);
                // If we can't poll, assume session is disconnected
                handleSessionDisconnect(sessionId, false);
            }
        }
    }, 50); // Poll every 50ms
}

function handleSessionDisconnect(sessionId, wasLogout) {
    if (!sessions[sessionId]) return;
    
    console.log(`Handling disconnect for session ${sessionId}, logout: ${wasLogout}`);
    
    // Stop polling
    if (outputPollingInterval) {
        clearInterval(outputPollingInterval);
        outputPollingInterval = null;
    }
    
    // Update UI
    const message = wasLogout ? 
        '\r\n\r\n[Session ended - User logged out]\r\n' : 
        '\r\n\r\n[Session ended - Connection lost]\r\n';
    
    if (sessions[sessionId].terminal) {
        sessions[sessionId].terminal.write(message);
    }
    
    // Mark session as disconnected
    sessions[sessionId].connected = false;
    
    // Update sessions list
    updateSessionsList();
    
    // Update status bar if this is the current session
    if (currentSessionId === sessionId) {
        document.getElementById('statusBar').style.display = 'none';
        
        // Show reconnect option after a delay
        setTimeout(() => {
            if (!sessions[sessionId].connected) {
                if (confirm('Connection lost. Would you like to reconnect?')) {
                    reconnectSession(sessionId);
                } else {
                    // Remove the session if user doesn't want to reconnect
                    removeSession(sessionId);
                }
            }
        }, 1000);
    }
    
    // Auto-remove session after 30 seconds if still disconnected
    setTimeout(() => {
        if (sessions[sessionId] && !sessions[sessionId].connected) {
            removeSession(sessionId);
        }
    }, 30000);
}

function removeSession(sessionId) {
    console.log(`Removing session ${sessionId}`);
    
    if (sessions[sessionId]) {
        // Cleanup terminal
        if (sessions[sessionId].terminal) {
            sessions[sessionId].terminal.dispose();
        }
        
        // Remove from sessions
        delete sessions[sessionId];
        updateSessionsList();
        
        // If this was the current session, show welcome screen
        if (currentSessionId === sessionId) {
            currentSessionId = null;
            currentTerminal = null;
            document.getElementById('terminalWrapper').style.display = 'none';
            document.getElementById('welcomeScreen').style.display = 'flex';
            document.getElementById('statusBar').style.display = 'none';
        }
    }
}

async function reconnectSession(oldSessionId) {
    const session = sessions[oldSessionId];
    if (!session) return;
    
    console.log(`Reconnecting session ${oldSessionId}`);
    
    // Fill in connection details
    document.getElementById('hostname').value = session.hostname;
    document.getElementById('username').value = session.username;
    
    // Remove old session
    removeSession(oldSessionId);
    
    // Connect
    await connect();
}

function switchToSession(sessionId) {
    currentSessionId = sessionId;
    
    // Hide all terminals
    document.getElementById('welcomeScreen').style.display = 'none';
    document.getElementById('connectingScreen').style.display = 'none';
    document.getElementById('terminalWrapper').style.display = 'block';
    
    // Show status bar
    document.getElementById('statusBar').style.display = 'flex';
    document.getElementById('statusHost').textContent = sessions[sessionId].hostname;
    
    // Focus terminal and force resize
    if (sessions[sessionId].terminal) {
        currentTerminal = sessions[sessionId].terminal;
        currentTerminal.focus();
        
        // Force fit after switching
        setTimeout(() => {
            if (sessions[sessionId].fitAddon) {
                try {
                    sessions[sessionId].fitAddon.fit();
                } catch (e) {
                    console.error('Error fitting terminal on switch:', e);
                }
            }
        }, 100);
        
        startOutputPolling(sessionId);
    }
    
    updateSessionsList();
}

function updateSessionsList() {
    const container = document.getElementById('sessionsList');
    container.innerHTML = '';
    
    const sessionValues = Object.values(sessions);
    if (sessionValues.length === 0) {
        container.innerHTML = '<div class="empty-message">No active sessions</div>';
        return;
    }
    
    // Open the active sessions section if there are sessions
    if (sessionValues.length > 0) {
        document.getElementById('activeSessionsContent').classList.add('open');
        document.getElementById('activeSessionsChevron').classList.add('open');
    }
    
    sessionValues.forEach(session => {
        const item = document.createElement('div');
        const isActive = session.id === currentSessionId;
        const isConnected = session.connected !== false;
        
        item.className = 'session-item' + (isActive ? ' active' : '') + (!isConnected ? ' disconnected' : '');
        item.innerHTML = `
            <div class="session-status" style="background: ${isConnected ? '#00ff88' : '#ff4444'}"></div>
            <div class="session-info">
                <div class="session-name">${session.username}@${session.hostname}</div>
                <div class="session-host">Session ${session.id.split('_')[1]} ${!isConnected ? '(Disconnected)' : ''}</div>
            </div>
            <div class="session-actions" style="opacity: 0; transition: opacity 0.2s ease;">
                ${isConnected ? 
                    '<button class="action-btn" onclick="disconnectSession(\'' + session.id + '\'); event.stopPropagation();">Disconnect</button>' :
                    '<button class="action-btn" onclick="removeSession(\'' + session.id + '\'); event.stopPropagation();">Remove</button>'
                }
            </div>
        `;
        
        if (isConnected) {
            item.onclick = () => switchToSession(session.id);
        } else {
            // For disconnected sessions, show reconnect option
            item.onclick = () => {
                if (confirm('This session is disconnected. Reconnect?')) {
                    reconnectSession(session.id);
                }
            };
        }
        
        // Show actions on hover
        item.onmouseenter = () => {
            const actions = item.querySelector('.session-actions');
            if (actions) actions.style.opacity = '1';
        };
        item.onmouseleave = () => {
            const actions = item.querySelector('.session-actions');
            if (actions) actions.style.opacity = '0';
        };
        
        container.appendChild(item);
    });
}

async function disconnectSession(sessionId) {
    if (confirm('Are you sure you want to disconnect this session?')) {
        console.log(`Manually disconnecting session ${sessionId}`);
        
        try {
            // Call API to disconnect
            await window.pywebview.api.disconnect(sessionId);
            
            // Handle as disconnection
            handleSessionDisconnect(sessionId, true);
        } catch (error) {
            console.error('Error disconnecting session:', error);
            // Force local disconnect
            handleSessionDisconnect(sessionId, false);
        }
    }
}

// Window resize handling
window.addEventListener('resize', () => {
    if (currentTerminal && sessions[currentSessionId]) {
        setTimeout(() => {
            if (sessions[currentSessionId].calculateSize) {
                sessions[currentSessionId].calculateSize();
            }
        }, 100);
    }
});