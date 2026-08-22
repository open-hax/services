;; SPDX-License-Identifier: GPL-3.0-or-later
(ns knoxx.backend.repl)

(defonce keepalive* (atom nil))

(defn main []
  (when-not @keepalive*
    (reset! keepalive*
            (js/setInterval (fn [] nil) 60000)))
  (js/console.log "Knoxx side-effect-free CLJS REPL runtime connected."))
