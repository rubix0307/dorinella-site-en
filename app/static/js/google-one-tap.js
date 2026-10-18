// Google One Tap on the sign-in page only. The credential goes to our server (which has Google check it); after a good
// answer the page reloads and the sign-in page forwards the person to their page.
(() => {
    const me = document.currentScript;
    const url = me.dataset.url, clientId = me.dataset.clientId;
    let tries = 0;

    async function signIn({credential}) {
        try {
            const reply = await fetch(url, {method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({credential})});
            if (reply.ok) location.reload();
        } catch { /* the person can still use the sign-in page */ }
    }

    function start() {
        const id = window.google?.accounts?.id;
        if (!id) {
            if (++tries < 40) setTimeout(start, 250);      // the Google script is still loading
            return;
        }
        id.initialize({client_id: clientId, callback: signIn, auto_select: false, cancel_on_tap_outside: true,
                       use_fedcm_for_prompt: true, itp_support: true});
        id.prompt();
    }

    start();
})();
