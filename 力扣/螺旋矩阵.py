class Solution():
    def spiralOrder(self, matrix):
        """
        :type matrix: List[List[int]]
        :rtype: List[int]
        """
        m,n=len(matrix),len(matrix[0])
        top,bottom,left,right=0,m-1,0,n-1
        result=[]
        while top<=bottom and left<=right:
            result.extend(matrix[top][left:right+1])
            top+=1
            for i in range(top,bottom+1):
                result.append(matrix[i][right])
            right-=1
            if top<=bottom:
                result.extend(matrix[bottom][left:right+1][::-1])
                bottom-=1
            if left<=right:
                for i in range(bottom,top-1,-1):
                    result.append(matrix[i][left])
                left+=1
        return result