export const STANDALONE_DATA_PREPARATION = process.env.NEXT_PUBLIC_STANDALONE_DATA_PREPARATION === "true";
export const PREPARATION_PATH = STANDALONE_DATA_PREPARATION ? "/prepare" : "/dashboard/workbench/database-setup";
