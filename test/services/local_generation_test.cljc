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
  (is (thrown? Exception (law/canonical 1.5)))
  (is (thrown? Exception (law/canonical {:unknown 1}))))

(deftest unknown-schema-and-approval-deny
  (is (seq (law/errors "plan" {"schema" "services.local-plan/v1"})))
  (is (seq (law/errors "approval" {"schema" "services.local-approval/v1"
                                     "extra" true}))))

(let [{:keys [fail error]} (run-tests)]
  (when (pos? (+ fail error)) (System/exit 1)))
