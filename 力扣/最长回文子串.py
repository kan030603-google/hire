class Solution:
    def longestPalindrome(self, s: str) -> str:
        result=[]
        n=len(s)
        for i in range(len(s)):
            left,right=i,i
            while left>=0 and right<=n-1 and s[left]==s[right]:
                left-=1
                right+=1
            if right-left-1>len(result):
                result=s[left+1:right]

            left=i-1
            right=i
            if s[left]==s[right]: 
                while left>=0 and right<=n-1 and s[left]==s[right]:
                    left-=1
                    right+=1
            if right-left-1>len(result):
                result=s[left+1:right]
        return result