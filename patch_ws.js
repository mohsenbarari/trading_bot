const fs = require('fs');
const path = './frontend/src/composables/chat/useChatWebSocket.ts';
let code = fs.readFileSync(path, 'utf8');

code = code.replace(`
        if (senderId) {
            typingUsers.value[senderId] = false;
            
            // Fix: Mark messages as read and reload immediately if sender matches selected chat
            if (selectedUserId.value && (senderId === selectedUserId.value)) {
                loadMessages(selectedUserId.value, true).then(() => {
                    markAsRead();
                    // Optional: scrollToBottom if near bottom
                });
            }
        }
`, `
        if (senderId) {
            typingUsers.value[senderId] = false;
            
            // Defensively cast to Numbers to avoid strict equality bugs
            const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null;
            const arrivingSender = Number(senderId);

            if (currentSelected !== null && arrivingSender === currentSelected) {
                loadMessages(currentSelected, true).then(() => {
                    markAsRead();
                });
            }
        }
`);

fs.writeFileSync(path, code);
