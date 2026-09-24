import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const PORT=Number(process.env.PORT||3000),DIR=path.dirname(fileURLToPath(import.meta.url));
const files={
  "/":{path:"index.html",type:"text/html; charset=utf-8"},
  "/app.js":{path:"app.js",type:"application/javascript; charset=utf-8"},
  "/vendor/cantojpmin_data.js":{path:"vendor/cantojpmin_data.js",type:"application/javascript; charset=utf-8"},
  "/vendor/cantojpmin_functions.js":{path:"vendor/cantojpmin_functions.js",type:"application/javascript; charset=utf-8"}
};
const cache=new Map();for(const [route,meta] of Object.entries(files)){cache.set(route,await fs.readFile(path.join(DIR,meta.path)));}
const appText=cache.get("/app.js").toString("utf8");new Function(appText);
for(const forbidden of ["/api/tts","x-cantonese-key","apiKey","fe"+"tch("]){if(appText.includes(forbidden))throw new Error("Zero-external guard failed: "+forbidden);}
function send(res,status,body,type){res.writeHead(status,{"content-type":type||"text/plain; charset=utf-8","cache-control":"no-store, no-cache, must-revalidate","pragma":"no-cache","x-content-type-options":"nosniff"});res.end(body);}
http.createServer((req,res)=>{const u=new URL(req.url,"http://localhost");console.log("[request]",req.method,u.pathname);
  if(req.method==="GET"&&u.pathname==="/api/health")return send(res,200,JSON.stringify({ok:true,version:"zero-external-3",externalApi:false,tts:"Web Speech API",jyutping:"CantoJpMin"}),"application/json; charset=utf-8");
  if(req.method!=="GET")return send(res,405,"Method not allowed");const meta=files[u.pathname];if(!meta)return send(res,404,"Not found");return send(res,200,cache.get(u.pathname),meta.type);
}).listen(PORT,"0.0.0.0",()=>console.log("Cantonese Coach Zero External on",PORT));