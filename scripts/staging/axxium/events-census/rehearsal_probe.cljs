(ns axxium.extern.rehearsal-probe
  "Read-only host qualification projection using the existing Axxium parser."
  (:require [axxium.domain.mailbox-events :as events]
            [axxium.law.did :as did]
            [axxium.law.event :as event]
            [axxium.shape.ledger-edn :as edn]
            [clojure.string :as str]))

(defn summarize
  "Read complete legacy and mailbox batch frames and return only custody counts."
  [text owner now-ms]
  (when-not (and (string? text) (or (empty? text) (str/ends-with? text "\n"))
                 (did/did? owner) (int? now-ms))
    (throw (ex-info "Invalid complete mailbox input" {})))
  (let [frames (edn/import-lines text)
        flatten-frame (fn [frame]
                        (let [items (cond
                                      (map? frame) [frame]
                                      (and (vector? frame) (= :axxium.mailbox/batch-v1 (first frame)))
                                      (subvec frame 1))]
                          (when-not (and (seq items) (every? event/valid-event? items))
                            (throw (ex-info "Invalid mailbox frame" {})))
                          items))
        ledger (into [] (mapcat flatten-frame) frames)]
    (clj->js (events/rehearsal-counts ledger owner (js/Date. now-ms)))))
