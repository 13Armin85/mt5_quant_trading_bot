#property strict

void SaveStatus()
{
   int file=FileOpen("noa-account-status.json",FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(file==INVALID_HANDLE) return;
   string data=StringFormat("{\"login\":%I64d,\"server\":\"%s\",\"trade_mode\":%d,\"connected\":%s,\"trade_allowed\":%s}",
      AccountInfoInteger(ACCOUNT_LOGIN),AccountInfoString(ACCOUNT_SERVER),
      (int)AccountInfoInteger(ACCOUNT_TRADE_MODE),
      TerminalInfoInteger(TERMINAL_CONNECTED)?"true":"false",
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)?"true":"false");
   FileWriteString(file,data);
   FileClose(file);
}

void OnStart()
{
   SaveStatus();
   for(int i=0;i<90 && !IsStopped() && !TerminalInfoInteger(TERMINAL_CONNECTED);i++) Sleep(1000);
   SaveStatus();
}
