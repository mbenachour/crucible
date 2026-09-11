import { createContext, useContext } from "react";
import type { NewRunPrefill } from "../components/NewRunModal";

/** Lets any page (e.g. a "try again" button on a failed launch) open the
 * global New-run modal that lives in the Shell, optionally pre-filled. */
export interface NewRunModalApi {
  open: (prefill?: NewRunPrefill) => void;
}

export const NewRunModalContext = createContext<NewRunModalApi>({ open: () => {} });

export const useNewRunModal = () => useContext(NewRunModalContext);
