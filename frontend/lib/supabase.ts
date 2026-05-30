import { createClient } from "@supabase/supabase-js";

const supabaseUrl = "https://ivxjowgozeshrsrldxtv.supabase.co";
const supabaseKey = "sb_publishable_fMe9AVgx5yIDY5uNKzA3Yw_SjZF8GXo";

export const supabase = createClient(supabaseUrl, supabaseKey);