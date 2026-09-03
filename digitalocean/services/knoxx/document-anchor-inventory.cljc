;; SPDX-License-Identifier: GPL-3.0-or-later

(ns document-anchor-inventory
  (:require [clojure.string :as str]
            #?(:clj [clojure.edn :as edn]
               :cljs [cljs.reader :as edn])
            #?(:clj [clojure.java.io :as io]
               :cljs ["node:fs" :as fs])
            #?(:cljs ["node:path" :as node-path])))

(def document-id-pattern #"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
(def whitespace-only-pattern
  #"^[\u0009-\u000D\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]*$")

(defn- fail! [file message]
  (throw (ex-info (str file ": " message) {:file file})))

(defn- documents-dir []
  #?(:clj (or (System/getenv "KNOXX_DOCUMENTS_DIR") "contracts/documents")
     :cljs (or (some-> js/process .-env .-KNOXX_DOCUMENTS_DIR)
               "contracts/documents")))

(defn- directory-files [directory]
  #?(:clj
     (let [root (io/file directory)]
       (when-not (.isDirectory root)
         (fail! directory "document resource directory is missing"))
       (->> (.listFiles root)
            (map #(.getName %))))
     :cljs
     (js->clj (.readdirSync fs directory))))

(defn- join-path [directory file]
  #?(:clj (.getPath (io/file directory file))
     :cljs (.join node-path directory file)))

(defn- validate-directory-entry! [directory name]
  (let [path (join-path directory name)
        [symlink? directory? regular-file?]
        #?(:clj (let [file (io/file path)]
                  [(java.nio.file.Files/isSymbolicLink (.toPath file))
                   (.isDirectory file)
                   (.isFile file)])
           :cljs (let [stat (.lstatSync fs path)]
                   [(.isSymbolicLink stat) (.isDirectory stat) (.isFile stat)]))]
    (when symlink?
      (fail! path "must be a regular non-symlink file"))
    (when directory?
      (fail! path "document resource subdirectories are not allowed"))
    (when-not regular-file?
      (fail! path "must be a regular non-symlink file"))
    (when (and (str/starts-with? name ".") (str/ends-with? name ".edn"))
      (fail! path "hidden EDN document resources are not allowed"))))

(defn- read-file [path]
  (when-not
    #?(:clj (let [file (io/file path)]
              (and (.isFile file)
                   (not (java.nio.file.Files/isSymbolicLink (.toPath file)))))
       :cljs (.isFile (.lstatSync fs path)))
    (fail! path "must be a regular non-symlink file"))
  #?(:clj (slurp path)
     :cljs (.readFileSync fs path "utf8")))

(defn- read-exactly-one-form [file content]
  ;; Wrapping the source in a vector forces the reader to consume the complete
  ;; input, unlike read-string on a bare form, which may ignore trailing forms.
  (let [forms (try
                (edn/read-string (str "[" content "\n]"))
                (catch #?(:clj Exception :cljs :default) error
                  (fail! file (str "invalid EDN: " #?(:clj (.getMessage error)
                                                       :cljs (.-message error))))))]
    (when-not (= 1 (count forms))
      (fail! file "resource must contain exactly one form"))
    (first forms)))

(defn- document-id [file document]
  (let [id (:document/id document)
        rendered (when (qualified-keyword? id)
                   (str (namespace id) "/" (name id)))]
    (when-not (and rendered (re-matches document-id-pattern rendered))
      (fail! file "must contain one qualified :document/id"))
    rendered))

(defn- validate-ownership! [file document]
  (let [visibility-present? (contains? document :document/visibility)
        visibility (:document/visibility document)
        org-present? (contains? document :document/org-id)
        org-id (:document/org-id document)
        nonblank-org? (and (string? org-id)
                           (not (re-matches whitespace-only-pattern org-id)))]
    (when (and visibility-present? (not (#{:public :private} visibility)))
      (fail! file ":document/visibility must be :public or :private"))
    (when (and org-present? (not nonblank-org?))
      (fail! file ":document/org-id must be a nonblank string"))
    (when (and (= :public visibility) nonblank-org?)
      (fail! file
             "must not declare both public visibility and an organization owner"))
    (when-not (or (= :public visibility) nonblank-org?)
      (fail! file
             "must declare :document/visibility :public or a nonblank :document/org-id"))))

(defn- validate-document! [file document]
  (when-not (map? document)
    (fail! file "resource must be a top-level map"))
  (when-not (true? (:document/anchor? document))
    (fail! file "must declare top-level :document/anchor? true"))
  (validate-ownership! file document)
  ;; This directory is the shared, deploy-authored publication corpus. The
  ;; admission request runs once as one automation actor, so accepting a
  ;; different organization's otherwise-valid anchor would discover the
  ;; mismatch only after the endpoint had persisted work for earlier files.
  ;; Organization-owned drafts are runtime state and never belong here.
  (when-not (= :public (:document/visibility document))
    (fail! file "Services-authored deployment anchors must be explicitly public"))
  (document-id file document))

(defn- inventory! []
  (let [directory (documents-dir)
        entries (->> (directory-files directory) sort vec)
        _ (doseq [entry entries]
            (validate-directory-entry! directory entry))
        files (->> entries
                   (filter #(str/ends-with? % ".edn"))
                   vec)]
    (when (empty? files)
      (fail! directory "contains no document resources"))
    (let [ids (mapv (fn [file]
                      (->> (read-file (join-path directory file))
                           (read-exactly-one-form file)
                           (validate-document! file)))
                    files)]
      (when-not (= (count ids) (count (distinct ids)))
        (fail! directory "contains duplicate :document/id values"))
      ids)))

(defn- exit-failure! [message]
  #?(:clj
     (do
       (binding [*out* *err*]
         (println (str "document anchor inventory failed: " message)))
       (System/exit 1))
     :cljs
     (do
       (.write (.-stderr js/process)
               (str "document anchor inventory failed: " message "\n"))
       (.exit js/process 1))))

(try
  ;; IDs are grammar-clamped above, so this is JSON without an escaping layer
  ;; that could disagree between the JVM test runner and production NBB.
  (println (str "[\"" (str/join "\",\"" (inventory!)) "\"]"))
  (catch #?(:clj Exception :cljs :default) error
    (exit-failure! #?(:clj (.getMessage error)
                      :cljs (.-message error)))))
