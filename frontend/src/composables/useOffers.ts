import { ref, computed } from 'vue';
import { useWebSocket } from './useWebSocket.ts';

const offers = ref<any[]>([]);
const isLoading = ref(false);
const error = ref('');
let pollingInterval: any = null;


// Singleton state (optional, but good if we want shared state across views)
// For now, let's keep it local to the function call or singleton if we want persistence.
// Given the requirements, users might switch between Dashboard and Market, so keeping it in a global state or store is better.
// But Pinia is not set up (or I didn't check). I'll use a simple shared state pattern for now.

const { on, connect } = useWebSocket();

// Real-time Event Handlers
function handleOfferCreated(data: any) {
    console.log('RT: Offer Created', data)

    // Add to list
    offers.value.unshift(data)

    // Play sound?
}
function handleOfferUpdated(data: any) {
    console.log('RT: Offer Updated', data);
    const index = offers.value.findIndex(o => o.id === data.id);
    if (index !== -1) {
        // Update fields
        offers.value[index] = { ...offers.value[index], ...data };
    }
}

function handleOfferExpired(data: any) {
    console.log('RT: Offer Expired', data);
    // Remove from list
    const id = data.id || data;
    offers.value = offers.value.filter(o => o.id !== id);
}

// Setup Listeners (Singleton)
on('offer:created', handleOfferCreated);
on('offer:updated', handleOfferUpdated);
on('offer:expired', handleOfferExpired);
on('offer:cancelled', handleOfferExpired);
on('offer:completed', handleOfferExpired);

export function useOffers() {

    async function fetchOffers(token: string | null, silent = false) {
        if (!token) return;
        if (!silent) isLoading.value = true;
        try {
            // Connect WS if not connected
            connect();

            const response = await fetch('/api/offers/', {
                headers: { Authorization: `Bearer ${token}` }
            });
            if (response.ok) {
                offers.value = await response.json();
            } else {
                throw new Error('Failed to fetch offers');
            }
        } catch (e: any) {
            error.value = e.message;
            console.error(e);
        } finally {
            if (!silent) isLoading.value = false;
        }
    }

    function startPolling(token: string | null) {
        if (pollingInterval) return;
        // Immediate fetch NOT required here as onMounted usually calls fetchOffers
        pollingInterval = setInterval(() => fetchOffers(token, true), 1000);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    }

    return {
        offers,
        isLoading,
        error,
        fetchOffers,
        startPolling,
        stopPolling
    };
}
