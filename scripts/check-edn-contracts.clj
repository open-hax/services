(require '[clojure.edn :as edn]
         '[clojure.java.io :as io])
(import '[java.io PushbackReader])

(def contract-root (io/file "contracts/knoxx"))
(def eof (Object.))
(def contract-files
  (->> (file-seq contract-root)
       (filter #(.isFile %))
       (filter #(.endsWith (.getName %) ".edn"))
       (sort-by #(.getPath %))))

(when-not (.isDirectory contract-root)
  (throw (ex-info "Knoxx contract root is missing"
                  {:path (.getPath contract-root)})))
(when (empty? contract-files)
  (throw (ex-info "Knoxx contract root contains no EDN resources"
                  {:path (.getPath contract-root)})))

(doseq [contract-file contract-files]
  (try
    (with-open [reader (PushbackReader. (io/reader contract-file))]
      (when (identical? eof (edn/read {:eof eof} reader))
        (throw (ex-info "resource is empty" {})))
      (when-not (identical? eof (edn/read {:eof eof} reader))
        (throw (ex-info "resource contains more than one form" {}))))
    (catch Exception error
      (throw (ex-info (str "invalid Knoxx EDN resource "
                           (.getPath contract-file) ": "
                           (.getMessage error))
                      {:path (.getPath contract-file)}
                      error)))))

(doseq [relative-path ["agents/publication_translator.edn"
                       "agents/publication_post_drafter.edn"]]
  (let [contract-file (io/file contract-root relative-path)
        contract (edn/read-string (slurp contract-file))]
    (when-not (= :required-first (:tools/choice contract))
      (throw (ex-info (str "publication agent must set top-level "
                           ":tools/choice :required-first: "
                           (.getPath contract-file))
                      {:path (.getPath contract-file)
                       :tools/choice (:tools/choice contract)})))))

(println "all" (count contract-files) "Knoxx EDN resources parse as exactly one form")
