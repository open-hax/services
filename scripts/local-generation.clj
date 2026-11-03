;; GPL-3.0-or-later. Experimental fixed private-sandbox facade; not installed.
;; Owner launch data comes ONLY from inherited fd 3, separate from rendered
;; request stdin. Empty request means recovery-only. No caller argv is allowed.
(in-ns 'local-generation)
(clojure.core/refer 'clojure.core)
(require '[babashka.fs :as fs]
             '[clojure.java.io :as io]
            '[cheshire.core :as json])
(import '[java.io ByteArrayOutputStream]
            '[java.security MessageDigest]
            '[java.math BigInteger]
           '[java.util.concurrent TimeUnit])

(def limit (* 16 1024 1024))
(defn bounded [stream maximum]
  (let [out (ByteArrayOutputStream.)
        buf (byte-array 8192)]
    (loop [total 0]
      (let [n (.read stream buf)]
        (if (neg? n)
          (.toByteArray out)
          (let [next-total (+ total n)]
            (when (> next-total maximum) (throw (ex-info "Bound" {})))
            (.write out buf 0 n)
            (recur next-total)))))))

(defn raw-sha [path]
  (format "%064x" (BigInteger. 1 (.digest (MessageDigest/getInstance "SHA-256")
                                        (fs/read-all-bytes path)))))

(defn read-deadline [stream]
  (let [task (future (bounded stream limit))]
    (try
      (let [value (deref task 10000 ::timeout)]
        (when (= ::timeout value) (throw (ex-info "Input deadline" {})))
        value)
      (finally (future-cancel task)))))

(defn refused []
  (json/generate-string
    {"schema" "services.local-result/v1" "action" "check" "status" "held"
     "plan" nil "plan_sha256" nil "services_revision" (apply str (repeat 40 "0"))
     "before" nil "after" nil "target_writes" nil "recovery" "uncertain"
     "first_failure" {"code" "recovery-uncertain" "stage" "input"
                        "detail" "Fixed worker unavailable or bounded transport failed."}
     "recovery_failures" [] "journal_sha256" nil}))

(defn run []
  (when (seq *command-line-args*) (throw (ex-info "Arguments" {})))
  (let [owner (with-open [input (io/input-stream "/proc/self/fd/3")] (read-deadline input))
        trusted (json/parse-string (String. owner "UTF-8"))
        here (fs/parent (fs/real-path *file*))
        sources {"facade" (fs/path here "local-generation.clj")
                 "runner" (fs/path here "local_generation_runner.py")
                 "edge" (fs/path here "local_generation_fs.py")
                 "wire" (fs/path here "local_generation_wire.py")
                 "validator" (fs/path here "local_generation_edn.py")
                 "laws" (fs/path here "local-generation-laws.clj")
                 "pure" (fs/path (fs/parent here) "src/services/local_generation.cljc")}
        pins (mapv (fn [[name path]] {"id" name "sha256" (raw-sha path)}) (sort-by key sources))]
    (when-not (and (= (set (keys trusted)) #{"schema" "policy" "revision" "sources" "python_sha256"})
                   (= "services.local-launch/v1" (trusted "schema"))
                   (= pins (trusted "sources"))
                   (= (raw-sha "/usr/bin/python3") (trusted "python_sha256")))
      (throw (ex-info "Custody" {})))
    (let [request (read-deadline System/in)
          builder (ProcessBuilder. ^java.util.List
                    ["/usr/bin/python3" "-I" "-B" (str (sources "runner"))])
          environment (.environment builder)]
      (.clear environment)
      (.put environment "PATH" "/usr/local/bin:/usr/bin:/bin")
      (.put environment "PYTHONDONTWRITEBYTECODE" "1")
      (.directory builder (io/file (str (fs/parent here))))
      (let [process (.start builder)
            writer (future (with-open [output (.getOutputStream process)]
                             (.write output (.getBytes (format "%08d" (alength owner)) "US-ASCII"))
                             (.write output owner) (.write output request)))
            output (future (bounded (.getInputStream process) limit))
            error (future (bounded (.getErrorStream process) 4096))]
        (try
          (when-not (.waitFor process 30 TimeUnit/SECONDS)
            (throw (ex-info "Deadline" {})))
          (when (= ::timeout (deref writer 1000 ::timeout)) (throw (ex-info "Writer" {})))
          (let [out (deref output 1000 ::timeout)
                err (deref error 1000 ::timeout)
                status (.exitValue process)]
            (when (or (= ::timeout out) (= ::timeout err) (pos? (alength err))
                      (not (#{0 2 3 4 5} status)))
              (throw (ex-info "Worker" {})))
            (.write System/out ^bytes out)
            (.flush System/out)
            status)
          (finally
            (when (.isAlive process) (.destroyForcibly process))
            (future-cancel writer) (future-cancel output) (future-cancel error)))))))

(let [status (try (run) (catch Exception _ (println (refused)) 5))]
  (shutdown-agents)
  (System/exit status))
