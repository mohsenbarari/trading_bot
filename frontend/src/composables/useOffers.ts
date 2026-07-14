import { ref } from 'vue';
import { useWebSocket } from './useWebSocket.ts';
import { apiFetch } from '../utils/auth';
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy';

const offers = ref<any[]>([]);
const isLoading = ref(false);
const isLoadingMore = ref(false);
const error = ref('');
const paginationError = ref('');
const nextCursor = ref<string | null>(null);
const hasMore = ref(false);
let pollingInterval: any = null;
let isFetching = false;
let queuedRefreshAfterCurrentFetch = false;
let queuedResetAfterCurrentFetch = false;
let refreshOffersFromServer: null | (() => Promise<void>) = null;
let filterRevision = 0;
let loadedAdditionalPages = false;

const ACTIVE_OFFERS_PAGE_SIZE = 50;

type ActiveOfferFilters = {
    offerType?: 'buy' | 'sell';
    settlementType?: 'cash' | 'tomorrow';
    commodityId?: number;
    ownOnly?: boolean;
};

let activeFilters: ActiveOfferFilters = {};



// Singleton state (optional, but good if we want shared state across views)
// For now, let's keep it local to the function call or singleton if we want persistence.
// Given the requirements, users might switch between Dashboard and Market, so keeping it in a global state or store is better.
// But Pinia is not set up (or I didn't check). I'll use a simple shared state pattern for now.

const { on, connect } = useWebSocket();

// Real-time Event Handlers
function handleOfferCreated(_data: any) {
    console.log('RT: Offer Created')
    if (refreshOffersFromServer) {
        void refreshOffersFromServer()
    }
}

function offerMatchesEvent(offer: any, data: any): boolean {
    const eventPublicId = typeof data?.offer_public_id === 'string'
        ? data.offer_public_id.trim()
        : '';
    const offerPublicId = typeof offer?.offer_public_id === 'string'
        ? offer.offer_public_id.trim()
        : '';
    if (eventPublicId && offerPublicId) return eventPublicId === offerPublicId;
    return offer?.id === data?.id;
}

function handleOfferUpdated(data: any) {
    console.log('RT: Offer Updated', data);
    const index = offers.value.findIndex(o => offerMatchesEvent(o, data));
    if (index !== -1) {
        const status = String(data?.status || '').toLowerCase();
        const hasRemainingQuantity = data?.remaining_quantity !== undefined
            && data?.remaining_quantity !== null
            && data?.remaining_quantity !== '';
        const remainingQuantity = Number(data?.remaining_quantity);
        if (
            status === 'completed'
            || status === 'expired'
            || status === 'cancelled'
            || (hasRemainingQuantity && Number.isFinite(remainingQuantity) && remainingQuantity <= 0)
        ) {
            offers.value = offers.value.filter(o => !offerMatchesEvent(o, data));
            return;
        }
        const current = offers.value[index];
        offers.value[index] = {
            ...current,
            ...data,
            // Database ids are server-local; public id is the cross-server identity.
            id: current.id,
            offer_public_id: current.offer_public_id || data?.offer_public_id,
        };
    }
}

function handleOfferExpired(data: any) {
    console.log('RT: Offer Expired', data);
    const event = data && typeof data === 'object' ? data : { id: data };
    offers.value = offers.value.filter(o => !offerMatchesEvent(o, event));
}

// Setup Listeners (Singleton)
on('offer:created', handleOfferCreated);
on('offer:updated', handleOfferUpdated);
on('offer:expired', handleOfferExpired);
on('offer:cancelled', handleOfferExpired);
on('offer:completed', handleOfferExpired);

export function useOffers() {

    function normalizeFilters(filters: ActiveOfferFilters): ActiveOfferFilters {
        const commodityId = Number(filters.commodityId);
        return {
            offerType: filters.offerType === 'buy' || filters.offerType === 'sell'
                ? filters.offerType
                : undefined,
            settlementType: filters.settlementType === 'cash' || filters.settlementType === 'tomorrow'
                ? filters.settlementType
                : undefined,
            commodityId: Number.isInteger(commodityId) && commodityId > 0 ? commodityId : undefined,
            ownOnly: filters.ownOnly === true || undefined,
        };
    }

    function filtersKey(filters: ActiveOfferFilters): string {
        return JSON.stringify([
            filters.offerType || '',
            filters.settlementType || '',
            filters.commodityId || 0,
            filters.ownOnly === true,
        ]);
    }

    function buildPageUrl(cursor?: string | null): string {
        const params = new URLSearchParams({ limit: String(ACTIVE_OFFERS_PAGE_SIZE) });
        if (activeFilters.offerType) params.set('offer_type', activeFilters.offerType);
        if (activeFilters.settlementType) params.set('settlement_type', activeFilters.settlementType);
        if (activeFilters.commodityId) params.set('commodity_id', String(activeFilters.commodityId));
        if (activeFilters.ownOnly) params.set('own_only', 'true');
        if (cursor) params.set('cursor', cursor);
        return `/api/offers/page?${params.toString()}`;
    }

    function offerIdentity(offer: any): string {
        const publicId = typeof offer?.offer_public_id === 'string' ? offer.offer_public_id.trim() : '';
        return publicId ? `public:${publicId}` : `local:${String(offer?.id ?? '')}`;
    }

    function dedupeOffers(rows: any[]): any[] {
        const seen = new Set<string>();
        return rows.filter((offer) => {
            const identity = offerIdentity(offer);
            if (seen.has(identity)) return false;
            seen.add(identity);
            return true;
        });
    }

    function activeRows(rows: any[]): any[] {
        const nowSec = Date.now() / 1000;
        return rows.filter((offer: any) => !offer.expires_at_ts || offer.expires_at_ts > nowSec);
    }

    async function readOfferPage(response: Response): Promise<{
        items: any[];
        nextCursor: string | null;
        hasMore: boolean;
    }> {
        const payload = await response.json();
        if (!payload || !Array.isArray(payload.items)) {
            throw new Error('Invalid active offer page response');
        }
        return {
            items: activeRows(payload.items),
            nextCursor: typeof payload.next_cursor === 'string' && payload.next_cursor
                ? payload.next_cursor
                : null,
            hasMore: payload.has_more === true,
        };
    }

    async function fetchOffers(silent = false) {
        if (isFetching) {
            queuedRefreshAfterCurrentFetch = true;
            if (!silent) queuedResetAfterCurrentFetch = true;
            return;
        }

        const token = localStorage.getItem('auth_token');
        if (!token) return;

        if (!silent) isLoading.value = true;
        isFetching = true;
        const requestRevision = filterRevision;

        try {
            const response = await apiFetch(buildPageUrl(), {
                cache: 'no-store',
                retryNetwork: false,
            });
            if (!response.ok) {
                throw await createHttpErrorFromResponse(response, {
                    surface: 'market',
                    scope: 'list',
                    operation: silent ? 'background-refresh' : 'load-list',
                    preserveExistingData: true,
                    resourceLabel: 'لیست لفظ‌ها',
                    fallbackMessage: 'دریافت لیست لفظ‌ها ممکن نشد.',
                })
            }

            const page = await readOfferPage(response);
            if (requestRevision === filterRevision) {
                if (silent && offers.value.length > 0) {
                    offers.value = dedupeOffers([...page.items, ...offers.value]);
                    if (!loadedAdditionalPages) {
                        nextCursor.value = page.nextCursor;
                        hasMore.value = page.hasMore;
                    }
                } else {
                    offers.value = dedupeOffers(page.items);
                    nextCursor.value = page.nextCursor;
                    hasMore.value = page.hasMore;
                    loadedAdditionalPages = false;
                }
                paginationError.value = '';
                error.value = '';
            }
        } catch (e: any) {
            // Don't clear offers on transient errors to prevent blinking
            console.error('Fetch offers error:', e);
            if (!silent) {
                error.value = getUserFacingErrorMessage(e, {
                    surface: 'market',
                    scope: 'list',
                    operation: 'load-list',
                    preserveExistingData: true,
                    resourceLabel: 'لیست لفظ‌ها',
                    fallbackMessage: 'دریافت لیست لفظ‌ها ممکن نشد.',
                });
            }
        } finally {
            if (!silent) isLoading.value = false;
            isFetching = false;
            if (queuedRefreshAfterCurrentFetch) {
                const queuedSilent = !queuedResetAfterCurrentFetch;
                queuedRefreshAfterCurrentFetch = false;
                queuedResetAfterCurrentFetch = false;
                void fetchOffers(queuedSilent);
            }
        }
    }

    async function loadMoreOffers() {
        if (isFetching || isLoadingMore.value || !hasMore.value || !nextCursor.value) return;

        const token = localStorage.getItem('auth_token');
        if (!token) return;

        const cursor = nextCursor.value;
        const requestRevision = filterRevision;
        isFetching = true;
        isLoadingMore.value = true;
        paginationError.value = '';
        try {
            const response = await apiFetch(buildPageUrl(cursor), {
                cache: 'no-store',
                retryNetwork: false,
            });
            if (!response.ok) {
                throw await createHttpErrorFromResponse(response, {
                    surface: 'market',
                    scope: 'list',
                    operation: 'load-more',
                    preserveExistingData: true,
                    resourceLabel: 'ادامه لفظ‌های بازار',
                    fallbackMessage: 'دریافت ادامه لفظ‌های بازار ممکن نشد.',
                });
            }
            const page = await readOfferPage(response);
            if (requestRevision === filterRevision) {
                offers.value = dedupeOffers([...offers.value, ...page.items]);
                nextCursor.value = page.nextCursor;
                hasMore.value = page.hasMore;
                loadedAdditionalPages = true;
                error.value = '';
            }
        } catch (e: any) {
            console.error('Load more offers error:', e);
            if (requestRevision === filterRevision) {
                paginationError.value = getUserFacingErrorMessage(e, {
                    surface: 'market',
                    scope: 'list',
                    operation: 'load-more',
                    preserveExistingData: true,
                    resourceLabel: 'ادامه لفظ‌های بازار',
                    fallbackMessage: 'دریافت ادامه لفظ‌های بازار ممکن نشد.',
                });
            }
        } finally {
            isLoadingMore.value = false;
            isFetching = false;
            if (queuedRefreshAfterCurrentFetch) {
                const queuedSilent = !queuedResetAfterCurrentFetch;
                queuedRefreshAfterCurrentFetch = false;
                queuedResetAfterCurrentFetch = false;
                void fetchOffers(queuedSilent);
            }
        }
    }

    async function setFilters(filters: ActiveOfferFilters) {
        const normalized = normalizeFilters(filters);
        if (filtersKey(normalized) === filtersKey(activeFilters)) return;
        activeFilters = normalized;
        filterRevision += 1;
        offers.value = [];
        nextCursor.value = null;
        hasMore.value = false;
        loadedAdditionalPages = false;
        error.value = '';
        paginationError.value = '';
        await fetchOffers(false);
    }

    function startPolling() {
        connect(); // Ensure WS is connected
        if (pollingInterval) return;
        refreshOffersFromServer = () => fetchOffers(true);
        fetchOffers(); // Initial fetch
        pollingInterval = setInterval(() => fetchOffers(true), 1000);
    }

    function stopPolling() {
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
        refreshOffersFromServer = null;
    }

    return {
        offers,
        isLoading,
        isLoadingMore,
        error,
        paginationError,
        hasMore,
        fetchOffers,
        loadMoreOffers,
        setFilters,
        startPolling,
        stopPolling
    };
}
