
;; Host-only rehearsal fixture export. The domain source above is pinned intact.
(defn rehearsal-counts
  "Project aggregate callback custody for one selected mailbox."
  [lg owner at]
  (let [view (project lg)
        sub (get-in view [:subscriptions owner])
        live-sub? (live? sub at)
        later #?(:cljs (js/Date. (+ (inst-ms at) attempt-lease-ms))
                 :clj (java.util.Date. (+ (inst-ms at) attempt-lease-ms)))
        pending (when sub (filter #(some? (eligible-message view sub % later))
                                  (get-in view [:accepted owner])))
        unresolved (filter #(and (= owner (:event/actor %))
                                 (not (contains? (:closed view)
                                                 (get-in % [:event/body :outbox/message-id]))))
                           (vals (:delivery view)))]
    {:retained-subscriptions (if sub 1 0)
     :active-subscriptions (if live-sub? 1 0)
     :eligible-pending (count pending)
     :retryable-callbacks (count (filter #(= :axxium.outbox/webhook-retry (:event/type %)) unresolved))
     :unsettled-attempts (count (filter #(= :axxium.outbox/webhook-attempted (:event/type %)) unresolved))
     :due-callbacks (count (filter #(= owner (:owner %)) (due-deliveries lg at)))}))
