const {defineConfig}=require('@playwright/test');
const path=require('node:path');
const os=require('node:os');
module.exports=defineConfig({
  testDir:'./tests/browser',workers:1,timeout:45000,
  use:{baseURL:'http://127.0.0.1:8765',channel:process.env.PLAYWRIGHT_CHANNEL||'chrome',headless:true,trace:'retain-on-failure'},
  webServer:{command:'python run.py',url:'http://127.0.0.1:8765',reuseExistingServer:false,env:{PORT:'8765',STOCKSENSE_DB:path.join(os.tmpdir(),`stocksense-browser-${Date.now()}.db`),STOCKSENSE_DEV_OTP:'1'}},
});
