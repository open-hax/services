(ns services.local-generation
  "Pure closed local-generation data laws. No filesystem/process authority."
  (:require [clojure.string :as str]))

(def max-int 9007199254740991)

(defn- bounded-decimal? [s limit]
  (and (string? s)
       (boolean (re-matches #"0|[1-9][0-9]*" s))
       (or (< (count s) (count limit))
           (and (= (count s) (count limit)) (not (pos? (compare s limit)))))))

(defn u64? [s] (bounded-decimal? s "18446744073709551615"))

(defn ns64? [s]
  (and (string? s)
       (if (str/starts-with? s "-")
         (and (not= s "-0") (bounded-decimal? (subs s 1) "9223372036854775808"))
         (bounded-decimal? s "9223372036854775807"))))

(defn- unit [c] #?(:clj (int c) :cljs (.charCodeAt c 0)))

(defn- utf8-length [s]
  (loop [units (map unit s) total 0]
    (if-let [u (first units)]
      (cond
        (zero? u) (throw (ex-info "NUL string" {:code :shape}))
        (< u 128) (recur (next units) (inc total))
        (< u 2048) (recur (next units) (+ total 2))
        (<= 55296 u 56319)
        (if (and (next units) (<= 56320 (second units) 57343))
          (recur (nnext units) (+ total 4))
          (throw (ex-info "Unpaired surrogate" {:code :shape})))
        (<= 56320 u 57343) (throw (ex-info "Unpaired surrogate" {:code :shape}))
        :else (recur (next units) (+ total 3)))
      total)))

(defn- bounded-int? [x] (and (integer? x) (<= 0 x max-int)))

(defn- string-value? [s]
  (and (string? s) (try (utf8-length s) true (catch #?(:clj Exception :cljs :default) _ false))))

(defn canonical
  "Versioned preimage value encoding, not EDN/JSON serialization or a digest.
  Caller hashes UTF8 bytes of (str domain-prefix (canonical value))."
  [value]
  (cond
    (nil? value) "N"
    (false? value) "F"
    (true? value) "T"
    (bounded-int? value) (str "I" value ";")
    (string? value) (str "S" (utf8-length value) ":" value)
    (vector? value) (str "V" (count value) ":" (apply str (map canonical value)))
    (map? value)
    (do
      (when-not (every? #(and (string? %) (re-matches #"[a-z][a-z0-9_]*" %)) (keys value))
        (throw (ex-info "Non-ASCII schema key" {:code :shape})))
      (str "M" (count value) ":"
           (apply str (mapcat (fn [[k v]] [(canonical k) (canonical v)]) (sort-by key value)))))
    :else (throw (ex-info "Outside canonical value domain" {:code :shape}))))

(def domain-prefix "services.local/v1\n")

(defn preimage [value] (str domain-prefix (canonical value)))

(defn- nullable [t] [:or :null t])
(defn- enum [& values] (into [:enum] values))
(defn- record [schema fields] (assoc fields "schema" [:enum schema]))

(def schemas
  {"pin" {"id" :id "repo" :string "revision" :sha40 "digest" :sha256}
   "identity" {"device" :u64 "inode" :u64 "mode" :int "uid" :int "gid" :int
               "size" :int "mtime_ns" :ns64 "ctime_ns" :ns64 "sha256" (nullable :sha256)}
   "anchor" {"components" [:vector :string] "identities" [:vector "identity"]}
   "artifact" {"path" :path "format" (enum "edn" "bash-env") "bytes_b64" :string
               "sha256" :sha256 "mode" [:enum 384] "renderer" "pin" "validator" "pin"
               "descriptor_b64" :string "descriptor_sha256" :sha256}
   "manifest" (record "services.local-manifest/v1"
                      {"owner" :id "generation" :id "artifacts" [:vector "artifact"]})
   "selection" (record "services.local-selection/v1"
                       {"owner" :id "generation" :id "manifest_sha256" :sha256})
   "xattr" {"name_b64" :string "value_b64" :string}
   "metadata" {"mode" :int "uid" :int "gid" :int "mtime_ns" :ns64 "xattrs" [:vector "xattr"]}
   "tree-entry" {"path" :path "identity" "identity" "metadata" "metadata"}
   "tree-proof" {"selection" "selection" "root_identity" "identity"
                 "entries" [:vector "tree-entry"] "tree_sha256" :sha256}
   "new-destination" {"kind" [:enum "new"] "identity" :null}
   "retained-destination" {"kind" [:enum "retained"] "proof" "tree-proof"}
   "observation" {"anchor" "anchor" "envelope" "identity" "lock" "identity"
                  "current" (nullable "identity") "selection" (nullable "selection")
                  "owned_tree_sha256" :sha256
                  "destination" [:or "new-destination" "retained-destination"]}
   "plan" (record "services.local-plan/v1"
                  {"operation" (enum "apply" "rollback") "owner" :id "root" :path "services" "pin"
                   "policy_sha256" :sha256 "manifest" "manifest" "expected" "observation"
                   "prior_manifest_sha256" (nullable :sha256) "created_at" :int "expires_at" :int "nonce" :id})
   "approval" (record "services.local-approval/v1"
                      {"plan_sha256" :sha256 "services_revision" :sha40 "owner" :id "root_sha256" :sha256
                       "operation" (enum "apply" "rollback") "expected_generation" (nullable :id)
                       "policy_sha256" :sha256 "issued_at" :int "expires_at" :int "approver" :id "nonce" :id})
   "seed" {"owner" :id "root" :path "services" "pin" "policy_sha256" :sha256 "manifest" "manifest"
           "operation" (enum "apply" "rollback") "expected_generation" (nullable :id)
           "created_at" :int "expires_at" :int "nonce" :id}
   "preview-request" (record "services.local-preview/v1" {"action" [:enum "preview"] "seed" "seed"})
   "request" (record "services.local-request/v1"
                     {"action" (enum "check" "apply" "rollback") "plan" "plan"
                      "approval" (nullable "approval") "plan_sha256" :sha256})
   "failure" {"code" (enum "shape" "limit" "pin" "validator" "approval" "expiry" "replay" "unsupported"
                            "ownership" "dirty" "ancestor-drift" "destination-drift" "current-drift"
                            "lock" "io" "interrupted" "recovery-uncertain")
              "stage" (enum "prepared" "building" "verified" "selecting" "selected" "committed"
                             "restoring" "restored" "held" "input" "preview" "check" "admission")
              "detail" :string}
   "result" (record "services.local-result/v1"
                    {"action" (enum "preview" "check" "apply" "rollback")
                     "status" (enum "previewed" "checked" "applied" "rolled-back" "denied" "unavailable" "failed-restored" "held")
                     "plan" (nullable "plan") "plan_sha256" (nullable :sha256) "services_revision" :sha40
                     "before" (nullable "selection") "after" (nullable "selection") "target_writes" (nullable :int)
                     "recovery" (enum "not-needed" "restored" "uncertain") "first_failure" (nullable "failure")
                     "recovery_failures" [:vector "failure"] "journal_sha256" (nullable :sha256)})
   "format-binding" {"format" (enum "edn" "bash-env") "renderer" "pin" "validator" "pin" "entry" :id}
   "policy" (record "services.local-policy/v1"
                    {"owner" :id "root" :path "anchor" "anchor" "services" "pin" "formats" [:vector "format-binding"]
                     "approvers" [:vector :id] "max_approval_ttl_ms" :int "max_artifacts" :int
                     "max_file_bytes" :int "max_total_bytes" :int})
   "capsule" (record "services.local-recovery/v1"
                     {"transaction" :id "plan" "plan" "approval" "approval" "policy" "policy"
                      "plan_sha256" :sha256 "approval_sha256" :sha256 "policy_sha256" :sha256 "services" "pin"
                      "before" (nullable "selection") "before_current" (nullable "identity")
                      "before_current_bytes_b64" (nullable :string) "before_metadata" (nullable "metadata")
                      "old_tree" (nullable "tree-proof") "rollback_target" (nullable "tree-proof")})
   "operation" {"index" :int "kind" (enum "mkdir" "write" "fsync" "rename" "unlink" "restore-metadata")
                "path" :path "before" (nullable "identity") "after" (nullable "identity")
                "state" (enum "pending" "succeeded" "failed" "uncertain")}
   "journal" (record "services.local-journal/v1"
                     {"transaction" :id "plan_sha256" :sha256 "approval_sha256" :sha256 "nonce" :id
                      "owner" :id "services" "pin" "policy_sha256" :sha256 "capsule_sha256" :sha256
                      "capsule_identity" "identity" "before" (nullable "selection") "before_current" (nullable "identity")
                      "before_current_bytes_b64" (nullable :string) "before_metadata" (nullable "metadata")
                      "candidate" "selection" "stage" (enum "prepared" "building" "verified" "selecting" "selected"
                                                               "committed" "restoring" "restored" "held")
                      "operations" [:vector "operation"] "first_failure" (nullable "failure")
                      "recovery_failures" [:vector "failure"] "last_time" :int})})

(defn- component? [s]
  (and (string-value? s) (<= 1 (utf8-length s) 128) (not (#{"." ".."} s))
       (not (re-find #"[/\\\x00-\x1f\x7f]" s))))

(defn- primitive? [type value]
  (case type
    :null (nil? value)
    :int (bounded-int? value)
    :string (string-value? value)
    :id (and (string? value) (boolean (re-matches #"[a-z0-9][a-z0-9-]{0,63}" value)))
    :sha40 (and (string? value) (boolean (re-matches #"[a-f0-9]{40}" value)))
    :sha256 (and (string? value) (boolean (re-matches #"[a-f0-9]{64}" value)))
    :u64 (u64? value)
    :ns64 (ns64? value)
    :path (and (vector? value) (<= (count value) 16) (every? component? value))
    false))

(declare errors)

(defn errors
  "Closed recursive shape errors; diagnostic paths contain schema keys only,
  never rejected values, artifact bytes or unknown caller-supplied keys."
  ([type value] (errors type value []))
  ([type value path]
   (let [shape (if (string? type) (get schemas type ::unknown) type)
         fail [{:code :shape :path path}]]
     (cond
       (= shape ::unknown) fail
       (keyword? shape) (if (primitive? shape value) [] fail)
       (map? shape)
       (if (and (map? value) (= (set (keys shape)) (set (keys value))))
         (into [] (mapcat (fn [[k t]] (errors t (get value k) (conj path k)))) shape)
         fail)
       (vector? shape)
       (case (first shape)
         :enum (if (some #(= value %) (rest shape)) [] fail)
         :or (if (some #(empty? (errors % value path)) (rest shape)) [] fail)
         :vector (if (and (vector? value) (<= (count value) 1100))
                   (into [] (mapcat (fn [[i v]] (errors (second shape) v (conj path i)))) (map-indexed vector value))
                   fail)
         fail)
       :else fail))))

(defn destination-valid?
  "Operation law only; runtime must independently re-observe actual custody."
  [plan]
  (let [destination (get-in plan ["expected" "destination"])
        manifest (get plan "manifest")
        selection (get-in plan ["expected" "selection"])]
    (case (get plan "operation")
      "apply" (= destination {"kind" "new" "identity" nil})
      "rollback" (and (= "retained" (get destination "kind"))
                      (= (get manifest "generation") (get-in destination ["proof" "selection" "generation"]))
                      (= (get plan "owner") (get-in destination ["proof" "selection" "owner"]))
                      (not= (get manifest "generation") (get selection "generation")))
      false)))

(defn approval-valid?
  "Pure approval comparisons using explicitly supplied independently computed
  digests and clock. This does not authenticate the trusted caller channel."
  [plan approval policy plan-hash root-hash policy-hash now]
  (and (empty? (errors "plan" plan)) (empty? (errors "approval" approval))
       (empty? (errors "policy" policy)) (bounded-int? now)
       (destination-valid? plan)
       (= (get approval "plan_sha256") plan-hash)
       (= (get approval "root_sha256") root-hash)
       (= policy-hash (get plan "policy_sha256") (get approval "policy_sha256"))
       (= (get plan "owner") (get approval "owner") (get policy "owner"))
       (= (get plan "root") (get policy "root"))
       (= (get plan "services") (get policy "services"))
       (= (get-in plan ["services" "revision"]) (get approval "services_revision"))
       (= (get plan "operation") (get approval "operation"))
       (= (get-in plan ["expected" "selection" "generation"]) (get approval "expected_generation"))
       (= (get plan "nonce") (get approval "nonce"))
       (some #(= (get approval "approver") %) (get policy "approvers"))
       (<= (get plan "created_at") (get approval "issued_at") now)
       (< now (get approval "expires_at"))
       (<= (get approval "expires_at") (get plan "expires_at"))
       (<= (- (get approval "expires_at") (get approval "issued_at"))
           (get policy "max_approval_ttl_ms") 900000)))
