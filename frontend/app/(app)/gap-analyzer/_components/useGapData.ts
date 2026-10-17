"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type GapAnalyzerResponse } from "@/lib/api";
import type { GapShow } from "./shared";

/** Both tabs load together so their labels carry counts. A failed refetch keeps `data`. */
export function useGapData(show: GapShow) {
  const [data, setData] = useState<GapAnalyzerResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const latestRequest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latestRequest.current;
    try {
      const response = await api.gapAnalyzer.get({
        modeAStatus: show,
        modeBStatus: show,
        modeASort: "coverage_asc",
        modeBSort: "signal_desc",
      });
      if (request !== latestRequest.current) return;
      setData(response);
      setFailed(false);
    } catch {
      if (request === latestRequest.current) setFailed(true);
    }
  }, [show]);

  useEffect(() => {
    setData(null);
    setFailed(false);
    void load();
  }, [load]);

  return { data, failed, load };
}
