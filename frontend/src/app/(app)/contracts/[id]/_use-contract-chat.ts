"use client";

// Connects the contract chat controller (lib/contract-chat-controller.ts) to React.
// The controller is made inside the effect, not during render, so React's
// double-mount in development gets a fresh one instead of a disposed one.

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { apiStream } from "@/lib/api";
import { assistantApi } from "@/lib/endpoints";
import { ContractChatController, INITIAL_VIEW, type ChatApi } from "@/lib/contract-chat-controller";

const noSubscribe = () => () => {};
const noSnapshot = () => INITIAL_VIEW;

export function useContractChat(contractId: string, onSettled: () => void) {
  // The newest callback, without making a new controller each time it changes.
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  });

  const [controller, setController] = useState<ContractChatController | null>(null);
  useEffect(() => {
    const c = new ContractChatController({
      contractId,
      api: assistantApi satisfies ChatApi,
      stream: apiStream,
      onSettled: () => settled.current(),
    });
    setController(c);
    void c.load();
    return () => {
      c.dispose();
      setController(null);
    };
  }, [contractId]);

  const view = useSyncExternalStore(
    controller ? controller.subscribe : noSubscribe,
    controller ? controller.getSnapshot : noSnapshot,
    controller ? controller.getSnapshot : noSnapshot,
  );
  return { controller, view };
}
