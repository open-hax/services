(ns services.local-generation-test
  (:require [clojure.test :refer [deftest is run-tests]]
            [services.local-generation :as law]))

(deftest lossless-native-observations
  (doseq [value ["0" "-1" "1793735800379596064"
                 "-9223372036854775808" "9223372036854775807"]]
    (is (law/ns64? value)))
  (doseq [value [0 "-0" "+1" "01" "9223372036854775808"]]
    (is (not (law/ns64? value))))
  (is (law/u64? "18446744073709551615"))
  (is (not (law/u64? "18446744073709551616")))
  (is (not= (law/canonical "1793735800379596064")
            (law/canonical "1793735800379596065"))))

(deftest canonical-contract
  (is (= "M2:S1:aS19:1793735800379596064S1:bV3:NI0;T"
         (law/canonical {"b" [nil 0 true] "a" "1793735800379596064"})))
  (is (= (law/canonical {"b" 2 "a" 1})
         (law/canonical {"a" 1 "b" 2})))
  (is (thrown? #?(:clj Exception :cljs js/Error) (law/canonical 1.5)))
  (is (thrown? #?(:clj Exception :cljs js/Error) (law/canonical {:unknown 1}))))

(deftest unknown-schema-and-approval-deny
  (is (seq (law/errors "plan" {"schema" "services.local-plan/v1"})))
  (is (seq (law/errors "approval" {"schema" "services.local-approval/v1"
                                     "extra" true}))))

(declare sample)

(defn sample [type]
  (let [shape (if (string? type) (get law/schemas type) type)]
    (cond
      (map? shape) (into {} (map (fn [[k t]] [k (sample t)])) shape)
      (vector? shape) (case (first shape)
                        :enum (second shape)
                        :or (sample (second shape))
                        :vector [])
      :else (case shape
              :id "fixture" :sha256 (apply str (repeat 64 "a"))
              :sha40 (apply str (repeat 40 "a")) :int 0 :path ["sandbox"]
              :ns64 "1793735800379596064" :u64 "18446744073709551615"
              :string "fixture" :null nil))))

(deftest every-closed-record
  (doseq [type (keys law/schemas)]
    (let [value (sample type)]
      (is (empty? (law/errors type value)) type)
      (is (seq (law/errors type (assoc value "unknown" "private value"))) type)
      (is (seq (law/errors type (dissoc value (first (keys value))))) type))))

(defn admitted-fixture []
  (let [hash (apply str (repeat 64 "a"))
        plan (-> (sample "plan")
                 (assoc "created_at" 1000 "expires_at" 100000 "operation" "apply")
                 (assoc-in ["expected" "destination"] {"kind" "new" "identity" nil}))
        policy (-> (sample "policy")
                   (assoc "services" (get plan "services") "approvers" ["fixture"]
                          "max_approval_ttl_ms" 900000))
        approval (-> (sample "approval")
                     (assoc "plan_sha256" hash "root_sha256" hash "policy_sha256" hash
                            "issued_at" 2000 "expires_at" 50000 "expected_generation" nil))]
    [plan approval policy hash hash hash 3000]))

(deftest independent-approval-bindings
  (let [[plan approval policy ph rh poh now :as args] (admitted-fixture)]
    (is (true? (boolean (apply law/approval-valid? args))))
    (doseq [key (keys approval)]
      (is (not (law/approval-valid? plan (assoc approval key "wrong") policy ph rh poh now)) key)))
  (let [[plan approval policy ph rh poh now] (admitted-fixture)]
    (is (not (law/approval-valid? plan approval policy ph rh poh 50000)))
    (is (not (law/approval-valid? plan approval policy ph rh poh 999)))
    (is (not (law/approval-valid? (assoc plan "operation" "rollback") approval policy ph rh poh now)))
    (is (not (law/approval-valid? plan approval (assoc policy "owner" "foreign") ph rh poh now)))))

(deftest retained-destination-is-operation-specific
  (let [plan (first (admitted-fixture))
        proof (assoc (sample "tree-proof") "selection"
                     (assoc (sample "selection") "generation" "a"))
        rollback (-> plan (assoc "operation" "rollback")
                     (assoc-in ["manifest" "generation"] "a")
                     (assoc-in ["expected" "selection"]
                               (assoc (sample "selection") "generation" "b"))
                     (assoc-in ["expected" "destination"] {"kind" "retained" "proof" proof}))]
    (is (law/destination-valid? plan))
    (is (law/destination-valid? rollback))
    (is (not (law/destination-valid? (assoc rollback "operation" "apply"))))
    (is (not (law/destination-valid? (assoc plan "operation" "rollback"))))))

(let [{:keys [fail error]} (run-tests)]
  (when (pos? (+ fail error))
    #?(:clj (System/exit 1) :cljs (set! (.-exitCode js/process) 1))))
