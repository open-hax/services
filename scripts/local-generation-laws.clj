;; GPL-3.0-or-later. Fixed pure-law transport, never an effect entry.
(in-ns 'local-generation-laws)
(clojure.core/refer 'clojure.core)
(require '[cheshire.core :as json]
         '[services.local-generation :as law])

(case (first *command-line-args*)
  "schemas" (println (json/generate-string
                       (mapv (fn [[name shape]] {"name" name "shape" shape})
                             (sort-by key law/schemas))))
  "approval"
  (let [v (json/parse-string (slurp *in*))]
    (println (json/generate-string
              (and (= (set (keys v)) #{"plan" "approval" "policy" "plan_hash"
                                        "root_hash" "policy_hash" "now"})
                   (law/approval-valid? (v "plan") (v "approval") (v "policy")
                                        (v "plan_hash") (v "root_hash")
                                        (v "policy_hash") (v "now"))))))
  (throw (ex-info "Unsupported law operation" {})))
